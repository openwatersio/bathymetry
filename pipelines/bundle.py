"""Concatenate the single-zoom PMTiles into a planet base + per-source overlays.

Everything at ``child_z <= PLANET_MAX_ZOOM`` (the GEBCO-native base cap, default =
macrotile_z) goes into a complete ``planet.pmtiles`` (z0..cap). Higher-res tiles
go into one ``<source>.pmtiles`` per **dominant high-res source** (e.g.
``cudem_ne.pmtiles``, z(cap+1)..source-max), so each source is independently
publishable. A ``manifest.json`` records planet + per-source coverage so the
serving Worker can resolve regional-vs-planet and overzoom the base on miss (see
SERVING / the plan's §Serving architecture).

Pure concat in tile-id order. Bundling everything (incremental rebuild is Phase E3).

Usage (from pipelines/):  bundle.py
"""

import json
import math
import os
import sys
from glob import glob

import mercantile
from pmtiles.tile import zxy_to_tileid, TileType, Compression
from pmtiles.reader import Reader, MmapSource, all_tiles
from pmtiles.writer import Writer

import config
import utils

# Base cap = the GEBCO-native zoom (the planet is complete + overzoomable below it).
PLANET_MAX_ZOOM = int(os.environ.get("PLANET_MAX_ZOOM", str(utils.macrotile_z)))


def high_res_sources(aggregation_id):
    """{source_id: {'bbox': [w,s,e,n], 'max_zoom': int}} for sources that own
    regional (child_z > PLANET_MAX_ZOOM) aggregation tiles. The owner of a tile is
    its deepest source (maxzoom == child_z), lex-first on a tie."""
    sources = {}
    for csv in glob(f"store/aggregation/{aggregation_id}/*-aggregation.csv"):
        z, x, y, child_z = (int(a) for a in csv.split("/")[-1].replace("-aggregation.csv", "").split("-"))
        if child_z <= PLANET_MAX_ZOOM:
            continue
        with open(csv) as f:
            rows = [line.strip().split(",") for line in f.readlines()[1:]]
        owner = sorted(s for s, fn, mz in rows if int(mz) == child_z)[0]
        w, s, e, n = mercantile.bounds(x, y, z)
        info = sources.setdefault(owner, {"bbox": [math.inf, math.inf, -math.inf, -math.inf], "max_zoom": 0})
        b = info["bbox"]
        b[0], b[1], b[2], b[3] = min(b[0], w), min(b[1], s), max(b[2], e), max(b[3], n)
        info["max_zoom"] = max(info["max_zoom"], child_z)
    return sources


def assign_source(z, x, y, sources):
    """Pick the deepest high-res source whose footprint the tile extent overlaps."""
    w, s, e, n = mercantile.bounds(x, y, z)
    best, best_mz = None, -1
    for src, info in sources.items():
        bw, bs, be, bn = info["bbox"]
        if w < be and e > bw and s < bn and n > bs and info["max_zoom"] > best_mz:
            best, best_mz = src, info["max_zoom"]
    return best


def group_filepaths(aggregation_id):
    """{'planet': [...], '<source>': [...]} grouping every single-zoom pmtiles."""
    sources = high_res_sources(aggregation_id)
    groups = {}
    for fp in sorted(glob("store/pmtiles/*.pmtiles") + glob("store/pmtiles/*/*.pmtiles")):
        z, x, y, child_z = (int(a) for a in fp.split("/")[-1].replace(".pmtiles", "").split("-"))
        name = "planet" if child_z <= PLANET_MAX_ZOOM else (assign_source(z, x, y, sources) or "planet")
        groups.setdefault(name, []).append(fp)
    return groups, sources


def read_full_archive(filepath):
    out = {}
    with open(filepath, "r+b") as f:
        reader = Reader(MmapSource(f))
        for tile_tuple, tile_bytes in all_tiles(reader.get_bytes):
            out[zxy_to_tileid(*tile_tuple)] = tile_bytes
    return out


def create_archive(filepaths, name):
    utils.create_folder("store/bundle")
    out_filepath = f"store/bundle/{name}.pmtiles"
    min_z, max_z = math.inf, 0
    min_lon, min_lat, max_lon, max_lat = math.inf, math.inf, -math.inf, -math.inf

    with open(out_filepath, "wb") as f1:
        hash_writer = utils.HashWriter(f1)
        writer = Writer(hash_writer)

        tile_ids_and_filepaths = []
        for filepath in filepaths:
            z, x, y, child_z = (int(a) for a in filepath.split("/")[-1].replace(".pmtiles", "").split("-"))
            parent = mercantile.Tile(x=x, y=y, z=z)
            tiles = [parent] if z == child_z else list(mercantile.children(parent, zoom=child_z))
            for tile in tiles:
                tile_ids_and_filepaths.append((zxy_to_tileid(tile.z, tile.x, tile.y), filepath))
            max_z, min_z = max(max_z, child_z), min(min_z, child_z)
            west, south, east, north = mercantile.bounds(x, y, z)
            min_lon, min_lat = min(min_lon, west), min(min_lat, south)
            max_lon, max_lat = max(max_lon, east), max(max_lat, north)

        last_filepath = None
        tile_id_to_bytes = None
        for tile_id, filepath in sorted(tile_ids_and_filepaths):
            if filepath != last_filepath:
                last_filepath = filepath
                tile_id_to_bytes = read_full_archive(filepath)
            writer.write_tile(tile_id, tile_id_to_bytes[tile_id])

        min_lon_e7, min_lat_e7 = int(min_lon * 1e7), int(min_lat * 1e7)
        max_lon_e7, max_lat_e7 = int(max_lon * 1e7), int(max_lat * 1e7)
        writer.finalize(
            {
                "tile_type": TileType.WEBP, "tile_compression": Compression.NONE,
                "min_zoom": min_z, "max_zoom": max_z,
                "min_lon_e7": min_lon_e7, "min_lat_e7": min_lat_e7,
                "max_lon_e7": max_lon_e7, "max_lat_e7": max_lat_e7,
                "center_zoom": int(0.5 * (min_z + max_z)),
                "center_lon_e7": int(0.5 * (min_lon_e7 + max_lon_e7)),
                "center_lat_e7": int(0.5 * (min_lat_e7 + max_lat_e7)),
            },
            {"attribution": utils.ATTRIBUTION},
        )
        checksum = hash_writer.md5.hexdigest()

    return {"file": f"{name}.pmtiles", "size": os.path.getsize(out_filepath), "md5sum": checksum,
            "min_zoom": min_z, "max_zoom": max_z,
            "bbox": [min_lon, min_lat, max_lon, max_lat]}


def attribution():
    """One HTML attribution string crediting every configured source. terrain and
    contours both come from the all-source merged DEM, so they share it.
    Lists all configured sources, not just those a regional BBOX actually touched —
    filter by manifest bbox intersection if a partial build ever needs exact credit."""
    parts = [utils.ATTRIBUTION]
    for sid in config.sources():
        m = config.load_metadata(sid)
        web, name = m.get("website"), m.get("name", sid)
        parts.append(f'<a href="{web}">{name}</a>' if web else name)
    return " | ".join(parts)


def bundle_group(item):
    name, filepaths = item
    print(f"bundling {name} ({len(filepaths)} pmtiles)...")
    return name, create_archive(filepaths, name)


def verify_complete(aggregation_id):
    """Every covering must have produced a pmtiles, or the pyramid has a silent hole.
    create_tile (aggregate) and run_one (downsample) emit one pmtiles per covering, so a
    covering with no pmtiles means a shard never ran or didn't sync — the Worker overzooms
    GEBCO into that hole, so it renders as missing high-zoom terrain. Fail rather than
    publish it. (downsampling.execute catches gaps a *running* shard sees; this catches a
    shard that produced nothing at all, which leaves nothing for execute to notice.)"""
    coverings = {
        c.split("/")[-1].replace("-aggregation.csv", "").replace("-downsampling.csv", "")
        for c in glob(f"store/aggregation/{aggregation_id}/*-aggregation.csv")
        + glob(f"store/aggregation/{aggregation_id}/*-downsampling.csv")
    }
    have = {fp.split("/")[-1].replace(".pmtiles", "")
            for fp in glob("store/pmtiles/*.pmtiles") + glob("store/pmtiles/*/*.pmtiles")}
    missing = sorted(coverings - have)
    if missing:
        raise SystemExit(
            f"pyramid incomplete: {len(missing)} of {len(coverings)} coverings have no pmtiles "
            f"(a failed/unsynced aggregate or downsample shard) — e.g. "
            f"{', '.join(missing[:15])}{' …' if len(missing) > 15 else ''}")


def _fragment(name, meta):
    """{kind, [id], ...meta} — one per-group manifest fragment so a matrix bundle job's
    metadata survives to the merge step (kind marks the planet base vs an overlay)."""
    kind = "planet" if name == "planet" else "source"
    return {"kind": kind, **({"id": name} if kind == "source" else {}), **meta}


def _manifest_from_fragments(frags):
    manifest = {"planet": None, "sources": []}
    for frag in frags:
        frag = dict(frag)
        if frag.pop("kind") == "planet":
            manifest["planet"] = frag
        else:
            manifest["sources"].append(frag)
    # deepest first so the Worker picks the highest-res overlay where they overlap.
    manifest["sources"].sort(key=lambda s: -s["max_zoom"])
    manifest["attribution"] = attribution()
    return manifest


def groups_matrix():
    """Verify the pyramid is whole, then print the group names as a JSON matrix (the CI
    spins one bundle job per group). Runs on the tail/plan runner, which holds the full
    store after the coarse tail; a hole here fails the build before any overlay ships."""
    aggregation_id = utils.get_aggregation_ids()[-1]
    verify_complete(aggregation_id)
    groups, _ = group_filepaths(aggregation_id)
    print(json.dumps(sorted(groups)))


def group_keys(name):
    """Write store/keys.txt: the R2 pmtiles keys belonging to one group, derived from the
    R2 listing (store/pmtiles-keys.txt) by the same rule group_filepaths uses on local
    files — so a matrix job pulls ONLY its group's slice, never the whole store."""
    aggregation_id = utils.get_aggregation_ids()[-1]
    sources = high_res_sources(aggregation_id)
    out = []
    with open("store/pmtiles-keys.txt") as f:
        for key in f:
            key = key.strip()
            if not key.endswith(".pmtiles"):
                continue
            try:
                z, x, y, child_z = (int(a) for a in key.split("/")[-1].replace(".pmtiles", "").split("-"))
            except ValueError:
                continue
            g = "planet" if child_z <= PLANET_MAX_ZOOM else (assign_source(z, x, y, sources) or "planet")
            if g == name:
                out.append(key)
    with open("store/keys.txt", "w") as f:
        f.write("".join(k + "\n" for k in out))
    print(f"group {name}: {len(out)} pmtiles selected")


def group(name):
    """Bundle one group from the tiles pulled locally (its slice only) + write its
    fragment. Disk stays bounded by one group's tiles + output, not the whole planet."""
    filepaths = sorted(glob("store/pmtiles/*.pmtiles") + glob("store/pmtiles/*/*.pmtiles"))
    _, meta = bundle_group((name, filepaths))
    utils.create_folder("store/bundle")
    with open(f"store/bundle/{name}.json", "w") as f:
        json.dump(_fragment(name, meta), f)


def merge():
    """Assemble manifest.json from the per-group fragments the matrix jobs produced."""
    frags = [json.load(open(jf)) for jf in sorted(glob("store/bundle/*.json"))]
    manifest = _manifest_from_fragments(frags)
    utils.create_folder("store/bundle")
    with open("store/bundle/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"merged manifest: planet + {len(manifest['sources'])} overlay(s)")


def main():
    """Local / single-runner: bundle every group sequentially, biggest first. The pmtiles
    writer spools each tile to a temp file then copies it into the archive (finalize ~2x's
    a bundle on disk), so building all groups at once piled every temp+final onto one disk
    and blew it at planet scale — CI fans this out per group (groups/group/merge) instead."""
    aggregation_id = utils.get_aggregation_ids()[-1]
    verify_complete(aggregation_id)
    groups, _ = group_filepaths(aggregation_id)
    frags = []
    for name, filepaths in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        _, meta = bundle_group((name, filepaths))
        frags.append(_fragment(name, meta))
    manifest = _manifest_from_fragments(frags)
    with open("store/bundle/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"created {len(groups)} bundle(s): {', '.join(groups)} + manifest.json")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        main()
    elif a[:1] == ["groups"]:
        groups_matrix()
    elif a[:1] == ["group-keys"] and len(a) == 2:
        group_keys(a[1])
    elif a[:1] == ["group"] and len(a) == 2:
        group(a[1])
    elif a[:1] == ["merge"]:
        merge()
    else:
        sys.exit("usage: bundle.py [groups | group-keys <name> | group <name> | merge]")
