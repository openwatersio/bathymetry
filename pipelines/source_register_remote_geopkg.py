"""Register a streaming source from a tile-scheme *GeoPackage* — NOAA BlueTopo's tile index,
where each feature's ``GeoTIFF_Link`` column is the per-tile COG URL (many features are
unpopulated → null link, dropped).

``file_list.txt`` holds either the tile-scheme prefix (``…/_BlueTopo_Tile_Scheme/`` — the
newest dated ``.gpkg`` under it is resolved via one public S3 list) or a direct ``.gpkg`` URL.
``BBOX`` (W,S,E,N lon/lat) pushes an OGR spatial filter on the tile geometry — BlueTopo tile
names aren't lat/lon-encoded, so geometry is the only prefilter.

The gpkg already *is* the index, so ``bounds.csv`` is built straight from it — footprint
geometry → 3857 bounds, ``Resolution`` → pixel size — with **no per-tile header reads** (7.4k
``/vsicurl`` round-trips would take ~40 min). See ``source_remote`` for the streaming model;
``source_register_remote_urllist`` is the header-read flat-urllist variant (CUDEM).

Run from pipelines/:  uv run python source_register_remote_geopkg.py <source-id>
"""

import math
import os
import re
import sys

import geopandas as gpd
import requests

import config
from source_remote import to_vsicurl, write_bounds


def _newest_key(list_xml):
    """The lexically-greatest ``.gpkg`` key in an S3 ListBucketResult XML, else None. BlueTopo's
    tile-scheme filenames are date-stamped, so lexical max == newest. Plain regex over <Key> (not
    an XML parser) — keys are simple and this sidesteps XXE/entity-expansion on the response."""
    gpkgs = sorted(k for k in re.findall(r"<Key>([^<]+)</Key>", list_xml) if k.endswith(".gpkg"))
    return gpkgs[-1] if gpkgs else None


def newest_gpkg(prefix_url):
    """Resolve a public-bucket prefix URL to its newest ``.gpkg`` (one S3 list, no creds). NOAA
    re-publishes BlueTopo's tile scheme under a stable prefix, so this tracks the current catalog
    without vendoring the dated filename."""
    host, _, prefix = prefix_url.partition(".s3.amazonaws.com/")
    host += ".s3.amazonaws.com"
    r = requests.get(host, params={"list-type": "2", "prefix": prefix}, timeout=60)
    r.raise_for_status()
    key = _newest_key(r.text)
    if not key:
        sys.exit(f"no .gpkg under {prefix_url}")
    return f"{host}/{key}"


def _populated_mask(links):
    """Boolean mask of populated tiles. pandas reads a NULL GeoTIFF_Link as float NaN — and
    ``bool(nan)`` is True — so test for a non-empty *string*, not bare truthiness. (~5k of
    BlueTopo's ~12.7k tiles are unpopulated.)"""
    return [isinstance(u, str) and bool(u) for u in links]


def _dims(ext_x_3857, ext_y_3857, lat, res_m):
    """Pixel width/height from a tile's 3857 extent + native metre resolution. 3857 metres are
    stretched ~1/cos(lat) vs ground metres, so the cos-corrected extent / resolution reproduces
    the pixel count a COG header would report — keeping the covering's maxzoom inference identical
    to the header-read path, without opening the tile."""
    cos = math.cos(math.radians(lat))
    return max(1, round(ext_x_3857 * cos / res_m)), max(1, round(ext_y_3857 * cos / res_m))


def gpkg_bounds(gpkg_url, bbox):
    """Build bounds.csv rows straight from the tile-scheme GeoPackage — no per-tile header reads.
    The gpkg indexes every tile (footprint geometry + Resolution + GeoTIFF_Link), so 3857 bounds
    come from reprojecting the footprint and pixel size from ``_dims``. ``bbox`` (W,S,E,N lon/lat)
    pushes an OGR spatial filter (gpkg geometry is WGS84) so a regional build reads only nearby rows."""
    gdf = gpd.read_file("/vsicurl/" + gpkg_url, bbox=tuple(bbox) if bbox else None)
    gdf = gdf[_populated_mask(gdf["GeoTIFF_Link"])]
    if gdf.empty:
        return []
    lat = gdf.geometry.representative_point().y          # tile center latitude (WGS84)
    b = gdf.geometry.to_crs(3857).bounds                 # vectorized reproject -> minx,miny,maxx,maxy
    rows = []
    for url, la, l, bot, r, t, resstr in zip(
            gdf["GeoTIFF_Link"], lat, b["minx"], b["miny"], b["maxx"], b["maxy"], gdf["Resolution"]):
        m = re.search(r"\d+(?:\.\d+)?", str(resstr))     # "16m" -> 16; default coarse if absent
        res_m = float(m.group()) if m else 16.0
        w, h = _dims(r - l, t - bot, la, res_m)
        rows.append((to_vsicurl(url), l, bot, r, t, w, h))
    return rows


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: source_register_remote_geopkg.py <source-id>")
    source = sys.argv[1]
    bbox = os.environ.get("BBOX", "").strip()
    bbox = [float(x) for x in bbox.split(",")] if bbox else None

    rows = []
    for manifest in config.file_list(source):
        gpkg = newest_gpkg(manifest) if manifest.endswith("/") else manifest
        print(f"reading tile-scheme gpkg {gpkg}")
        rows += gpkg_bounds(gpkg, bbox)
    write_bounds(source, rows)


def _check():
    sample = (
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<Contents><Key>BlueTopo/_BlueTopo_Tile_Scheme/BlueTopo_Tile_Scheme_20250101_000000.gpkg</Key></Contents>"
        "<Contents><Key>BlueTopo/_BlueTopo_Tile_Scheme/BlueTopo_Tile_Scheme_20260616_191529.gpkg</Key></Contents>"
        "<Contents><Key>BlueTopo/_BlueTopo_Tile_Scheme/index.html</Key></Contents>"
        "</ListBucketResult>"
    )
    assert _newest_key(sample).endswith("20260616_191529.gpkg"), _newest_key(sample)
    assert _newest_key("<ListBucketResult/>") is None
    # unpopulated tiles read as float NaN (truthy!) — must be masked out, not .lower()'d
    assert _populated_mask(["a.tif", "", float("nan"), "b.tiff"]) == [True, False, False, True]
    # pixel size: at the equator 3857 == ground; at 60°N (cos ½) a tile is half the px per 3857 m
    assert _dims(1000, 1000, 0.0, 10.0) == (100, 100)
    assert _dims(1000, 1000, 60.0, 10.0) == (50, 50)
    print("source_register_remote_geopkg.py self-check ok")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--check"]:
        _check()
    else:
        main()
