"""Register a remote COG tile collection as a source WITHOUT downloading it.

For data already published as Cloud-Optimized GeoTIFFs on a public bucket (e.g.
NOAA CUDEM): instead of bulk-fetching ~188 GB, read each tile's *header* via GDAL
``/vsicurl/`` and record its 3857 bounds in ``store/source/<id>/bounds.csv`` with
the ``/vsicurl/`` path itself as the "filename". The aggregation stage then
range-reads only the COG blocks it needs straight over public HTTPS
(``config.source_path`` passes the ``/vsicurl/`` path through — no credentials, so
it coexists with the signed-free R2 reads of the locally-prepared sources).

No download, no normalize (the tiles are already COGs with CRS + nodata), no
polygonize/tarball (a streaming source has no local bytes to redistribute).

``file_list.txt`` holds manifest URL(s) — text files of one tile URL per line
(CUDEM's ``urllist8483.txt``). ``BBOX`` (W,S,E,N lon/lat) prefilters by each tile's
name-encoded location so a regional build only probes nearby tiles.

Run from pipelines/:  uv run python source_register_remote.py <source-id>
"""

import os
import re
import sys

import rasterio
import requests
from rasterio.warp import transform_bounds

import config
import utils
from source_download_filelist import filelist_urls

def to_vsicurl(url):
    """An http(s)/s3:// URL -> a GDAL ``/vsicurl/`` path (public range reads, no creds)."""
    if url.startswith("s3://"):
        bucket, key = url[len("s3://"):].split("/", 1)
        url = f"https://{bucket}.s3.amazonaws.com/{key}"
    return "/vsicurl/" + url


def tile_lonlat(name):
    """(lon, lat) of a tile from a name like ``ncei19_n29x00_w089x25_...``, else None.

    Used only as a cheap BBOX prefilter; the covering re-filters precisely from the
    real header bounds, so over-inclusion is harmless and under-inclusion is what we
    must avoid — hence the generous margin in ``near``.
    """
    mlat = re.search(r"[_/]n(\d+)[xX](\d+)", name)  # names mix n39x00 and n25X75
    mlon = re.search(r"_w(\d+)[xX](\d+)", name)
    if not (mlat and mlon):
        return None
    lat = int(mlat.group(1)) + int(mlat.group(2)) / 100.0
    lon = -(int(mlon.group(1)) + int(mlon.group(2)) / 100.0)
    return lon, lat


def near(lonlat, bbox, margin=0.5):
    lon, lat = lonlat
    w, s, e, n = bbox
    return (w - margin) <= lon <= (e + margin) and (s - margin) <= lat <= (n + margin)


def bounds_3857(src):
    left, bottom, right, top = transform_bounds(src.crs, "EPSG:3857", *src.bounds)
    if right - left > 0.9 * 2 * utils.X_MAX_3857:  # antimeridian flip (e.g. Aleutians)
        left, right = right, left
    return left, bottom, right, top


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: source_register_remote.py <source-id>")
    source = sys.argv[1]
    bbox = os.environ.get("BBOX", "").strip()
    bbox = [float(x) for x in bbox.split(",")] if bbox else None

    urls = []
    for manifest in config.file_list(source):
        print(f"reading manifest {manifest}")
        r = requests.get(manifest, timeout=60)
        r.raise_for_status()
        urls += filelist_urls(r.text)
    # The manifest also lists sidecars (tile-index .shp/.shx/.dbf, .vrt, .xml, .pdf,
    # urllist itself); keep only the raster tiles.
    urls = [u for u in urls if u.lower().endswith(".tif")]

    os.makedirs(f"store/source/{source}", exist_ok=True)
    lines = ["filename,left,bottom,right,top,width,height\n"]
    kept = skipped = 0
    for url in urls:
        if bbox is not None:
            ll = tile_lonlat(url.rsplit("/", 1)[-1])
            if ll is not None and not near(ll, bbox):
                skipped += 1
                continue
        path = to_vsicurl(url)
        with rasterio.open(path) as src:
            if src.crs is None:
                sys.exit(f"crs not defined on {path}")
            left, bottom, right, top = bounds_3857(src)
            lines.append(f"{path},{left},{bottom},{right},{top},{src.width},{src.height}\n")
        kept += 1
        if kept % 100 == 0:
            print(f"  registered {kept} (skipped {skipped})")

    with open(f"store/source/{source}/bounds.csv", "w") as f:
        f.writelines(lines)
    print(f"{source}: registered {kept} remote tiles, skipped {skipped} outside BBOX")


if __name__ == "__main__":
    main()
