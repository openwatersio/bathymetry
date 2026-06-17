"""Shared core for *streaming* sources — COG tile collections already published on a
public bucket, registered WITHOUT downloading.

Instead of bulk-fetching the bytes, read each tile's *header* via GDAL ``/vsicurl/`` and
record its 3857 bounds in ``store/source/<id>/bounds.csv`` with the ``/vsicurl/`` path
itself as the "filename". The aggregation stage then range-reads only the COG blocks it
needs straight over public HTTPS (``config.source_path`` passes the ``/vsicurl/`` path
through — no credentials, so it coexists with the signed-free R2 reads of locally-prepared
sources). No normalize (tiles are already COGs with CRS + nodata), no polygonize/tarball
(a streaming source has no local bytes to redistribute).

Two enumeration shapes pick a front-end CLI. A flat text urllist
(``source_register_remote_urllist``, CUDEM) has no per-tile metadata, so it
``register_tiles`` — opening each header to read bounds + size. A tile-scheme GeoPackage
(``source_register_remote_geopkg``, BlueTopo) already *is* the index (footprint geometry +
resolution per tile), so it derives the bounds rows itself and calls ``write_bounds`` directly,
skipping ~7k header round-trips.
"""

import os
import sys

import rasterio
from rasterio.warp import transform_bounds

import utils


def to_vsicurl(url):
    """An http(s)/s3:// URL -> a GDAL ``/vsicurl/`` path (public range reads, no creds)."""
    if url.startswith("s3://"):
        bucket, key = url[len("s3://"):].split("/", 1)
        url = f"https://{bucket}.s3.amazonaws.com/{key}"
    return "/vsicurl/" + url


def bounds_3857(src):
    left, bottom, right, top = transform_bounds(src.crs, "EPSG:3857", *src.bounds)
    if right - left > 0.9 * 2 * utils.X_MAX_3857:  # antimeridian flip (e.g. Aleutians)
        left, right = right, left
    return left, bottom, right, top


def write_bounds(source, rows):
    """Write store/source/<source>/bounds.csv from rows of
    (vsicurl_path, left, bottom, right, top, width, height) — bounds in EPSG:3857. The covering
    re-filters precisely from these bounds, so a generous upstream BBOX prefilter is fine."""
    os.makedirs(f"store/source/{source}", exist_ok=True)
    with open(f"store/source/{source}/bounds.csv", "w") as f:
        f.write("filename,left,bottom,right,top,width,height\n")
        for path, left, bottom, right, top, width, height in rows:
            f.write(f"{path},{left},{bottom},{right},{top},{width},{height}\n")
    print(f"{source}: wrote {len(rows)} tiles to bounds.csv")


def register_tiles(source, urls):
    """Header-read each tile via /vsicurl -> 3857 bounds + pixel size -> bounds.csv. For sources
    with no metadata index (a flat urllist, e.g. CUDEM); a GeoPackage-indexed source builds rows
    from the index and calls write_bounds directly, skipping these per-tile reads."""
    rows = []
    for i, url in enumerate(urls):
        path = to_vsicurl(url)
        with rasterio.open(path) as src:
            if src.crs is None:
                sys.exit(f"crs not defined on {path}")
            left, bottom, right, top = bounds_3857(src)
            rows.append((path, left, bottom, right, top, src.width, src.height))
        if (i + 1) % 100 == 0:
            print(f"  read {i + 1}/{len(urls)} headers")
    write_bounds(source, rows)


def _check():
    assert to_vsicurl("s3://b/k/x.tif") == "/vsicurl/https://b.s3.amazonaws.com/k/x.tif"
    assert to_vsicurl("https://h.example/x.tif") == "/vsicurl/https://h.example/x.tif"
    print("source_remote.py self-check ok")


if __name__ == "__main__":
    _check()
