"""Pipeline configuration + source access.

Scripts run from the ``pipelines/`` directory; the
``store/`` working dirs are cwd-relative. Sources live in ``../sources/<id>/``,
each with ``metadata.json`` (attribution + bathymetry knobs) and ``file_list.txt``.
Ported from scripts/config.sh.
"""

import json
import os
from glob import glob

SOURCES_DIR = os.environ.get("SOURCES_DIR", "../sources")

# Non-uniform bathymetric contour levels (metres, most-negative first): fine in
# the shallows, coarse in the deep. Ported from scripts/config.sh.
CONTOUR_LEVELS = [int(x) for x in (
    "-10000 -8000 -6000 -5000 -4000 -3000 -2000 -1500 -1000 -500 -200 -150 "
    "-100 -75 -50 -45 -40 -35 -30 -25 -20 -15 -14 -13 -12 -11 -10 -9 -8 -7 -6 "
    "-5 -4 -3 -2 -1"
).split()]


def sources():
    """All source ids (directory names under SOURCES_DIR)."""
    return sorted(os.path.basename(p) for p in glob(f"{SOURCES_DIR}/*") if os.path.isdir(p))


def load_metadata(source):
    with open(f"{SOURCES_DIR}/{source}/metadata.json") as f:
        return json.load(f)


def source_path(source, filename):
    """A bounds.csv ``filename`` resolved to a GDAL-openable path. Three forms:

    1. The filename is already a full ``/vsi`` path (a streaming source like CUDEM,
       registered straight off a public bucket) — use it verbatim.
    2. ``SOURCE_VSI_BASE`` is set (the CI aggregate job reading prepared COGs from
       public R2) — resolve ``<base>/<source>/<filename>``, e.g.
       ``/vsicurl/https://tiles.openwaters.io/store/source/gebco/gebco_0.tif``.
    3. Otherwise (local dev) — ``store/source/<source>/<filename>`` on disk.

    So locally-prepared sources stream from R2 in CI yet read from disk locally,
    with no change to how they're prepared. Everything is public ``/vsicurl`` — no
    credentials in the read path.
    """
    if filename.startswith("/vsi"):
        return filename
    base = os.environ.get("SOURCE_VSI_BASE")
    if base:
        return f"{base}/{source}/{filename}"
    return f"store/source/{source}/{filename}"


def file_list(source):
    path = f"{SOURCES_DIR}/{source}/file_list.txt"
    if not os.path.isfile(path):
        return []
    with open(path) as f:
        return [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
