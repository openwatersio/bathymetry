# Priority-Mosaic Plan — Multi-Source Bathymetry Coverage

Goal: one terrain-RGB tileset + one contour tileset that uses **GEBCO 2026 as the
global base** and **defers to higher-quality regional data where it exists**,
extending to **deeper zoom only where the data supports it**.

> **Status (2026-06):** the *engine* described by the old version of this plan has
> been rebuilt. The monolithic bash `scripts/` pipeline (VRT priority, disjoint
> zoom bands, sqlite `INSERT OR IGNORE` union) is **retired**; the port to the
> Python four-stage pipeline (`source → aggregation → downsampling → bundle`) +
> serving Worker shipped in `000b53b`. See the port plan
> (`~/.claude/plans/compiled-honking-toast.md`) for the why and [CLAUDE.md](CLAUDE.md)
> for the as-built architecture. **This doc is now the source/coverage roadmap** —
> which data goes in, at what priority and zoom, and what's left to add — not an
> architecture doc.

## Core idea (as built)

The same two mechanisms the original plan called for, re-expressed in the new
pipeline (both live in `pipelines/`, not `scripts/`):

1. **Priority handled in the aggregation merge.** Per aggregation tile,
   `aggregation_run.py` reprojects each source into a merged Float32 DEM, drawing
   higher-priority sources on top and letting lower ones fill nodata, with a
   Gaussian seam feather. "Defer to higher quality" is now a deterministic merge,
   not `gdalbuildvrt`. **Priority is *derived*, not configured:** `(maxzoom, id)`
   — GEBCO has the smallest maxzoom so it loses everywhere a regional source
   overlaps; ties break lexically on id.

2. **Variable zoom handled by a planet cap + per-source overlays + a Worker.**
   The all-sources-merged base is complete to `macrotile_z` (`PLANET_MAX_ZOOM`,
   currently GEBCO-native ~z8) in `planet.pmtiles`. Each high-res source's deeper
   tiles bundle into a `<source>.pmtiles` overlay (carrying the GEBCO-filled
   mosaic — Terrarium has no transparency, so overlays must not punch holes). The
   `worker/` Cloudflare Worker resolves per tile: z≤cap → planet; z>cap covered →
   overlay; else → overzoom the planet. One endpoint, no global GEBCO upsampling,
   no holes. This **supersedes** the old "disjoint zoom bands + byte-identical
   union into one pmtiles" model.

Terrain encode is **Terrarium + per-zoom quantization** (`encode.py`), not
`rio-rgbify`. Contours run as a parallel consumer of the same merged DEM
(`contour_run.py`); cross-tile line continuity comes from **buffer the DEM input,
restrict the tile output**.

## Source ingest: `/vsicurl/` streaming over public buckets ✅ DONE (CI verify pending)

The problem CUDEM surfaced: source bytes land **twice** — once when the source step
downloads them, then again when *every* aggregate shard `aws s3 sync`s the whole
`store/source` from R2 before reprojecting. GEBCO+EMODnet+DDM are tens of GB (fits),
but CUDEM's ~188 GB blows both. The fix, generalized to every source: **reproject
range-reads each source COG over public HTTPS via GDAL `/vsicurl/` — nothing lands
in bulk.** Because both buckets are public, there are *no credentials in the read
path*, which sidesteps the one real trap (a global `AWS_NO_SIGN_REQUEST` for NOAA
would break signed R2 reads — so everything is `/vsicurl`, never `/vsis3`).

Two source shapes, one read path:

- **Already-COG public bucket (CUDEM):** `source_register_remote.py` reads each tile's
  *header* via `/vsicurl/` and writes its `bounds.csv` row with the full `/vsicurl/`
  NOAA URL as the filename. No download, no normalize, no tarball.
- **Prepared sources (GEBCO/EMODnet/DDM):** the source stage still fetches → unzips →
  bakes transforms (DDM `negate`, etc.) → writes a local COG → and the **existing CI
  step already uploads `store/source/<id>` to R2**. `bounds.csv` keeps basenames; at
  aggregate time `SOURCE_VSI_BASE=/vsicurl/https://tiles.openwaters.io/store/source`
  resolves them to public-R2 URLs.

[`config.source_path`](pipelines/config.py) resolves all three cases (full `/vsi` path
→ verbatim; `SOURCE_VSI_BASE` set → R2 URL; else local disk), so **local dev reads
from disk unchanged** while CI streams. The CI aggregate job **drops the
`aws s3 sync …/store/source`** entirely. COG internal tiling + overviews make the range
reads cheap; **R2 has zero egress** so CI reads are free; builds decouple from
NOAA/EMODnet/SDFI/CEDA uptime. Single-grid sources (GEBCO, DDM) have no *subset* win
but still shed the per-shard local copy.

**Validated locally:** `just preview` builds GEBCO(local)+CUDEM(`/vsicurl` NOAA) end to
end (`store/source/cudem` = 4 KB `bounds.csv`, 51 MB cudem overlay), and a real GEBCO
COG reads from public R2 via `/vsicurl/` (header + `gdalbuildvrt`, the reproject path).
Remaining: a CI run to confirm the aggregate shards read R2/NOAA without the source
sync. The per-source **tarball** is now redundant for streaming sources (the COG in its
bucket is the artifact) — leave for now, prune later.

## Source priority (worst → best, finer res wins)

| Source       | Native res | Zoom ceiling | Coverage      | Datum          | Status |
| ------------ | ---------- | ------------ | ------------- | -------------- | ------ |
| GEBCO 2026   | ~450 m     | ~z8 (cap)    | global        | MSL            | ✅ source #0 |
| EMODnet 2024 | ~115 m     | z11          | European seas | LAT (confirm)  | ✅ ingest (58-tile) |
| DDM (Denmark)| 50 m       | z12          | Danish EEZ    | MSL (DKMSL2022)| ✅ ingest (`--negate`) |
| CUDEM 1/9    | ~3.4 m     | z13          | US coast      | NAVD88         | ✅ `cudem` (942-tile manifest) |
| BlueTopo     | 2–16 m     | z14–15       | US navigable  | MLLW           | ⬜ not built |
| CUDEM 1/3    | ~10 m      | z11–12       | US coast (broader) | NAVD88    | ⬜ optional coarse fill |
| CUDEM terr.  | ~3.4 m     | z13          | HI/PR/USVI/Guam/AmSam/CNMI | NAVD88 | ⬜ own products |
| NIWA NZ      | 250 m      | z10          | NZ EEZ        | varies         | ⬜ not built |

Zoom ceilings are display caps, not native res (BlueTopo's 2 m ≈ z18; we cap
where it stops being worth the tile count) — set per source via the optional
`max_zoom` in `metadata.json`, else inferred from pixel size. Priority is derived
from `(maxzoom, id)`; the explicit column is gone. **Open risk:** DDM and EMODnet
must keep DDM winning in Danish waters — if their inferred maxzooms tie, the
lex-on-id tiebreak (`ddm` < `emodnet`) happens to favor DDM, but a real source
ever sorting wrong needs a manual tiebreaker (port-plan risk #3). **DDM stores
positive depth** → its recipe runs `source_datum.py --negate`.

---

## Phase 0 — GEBCO base ✅ DONE

GEBCO 2026 is the configured global grid and the best today (SWOT+ML deep ocean,
newer than ETOPO 2022). It's `sources/gebco/` — source #0, no special-casing.

## Phase 1 — Source abstraction + single-region proof ✅ DONE (superseded)

The original bash prototype proved priority + zoom-bands end to end with GEBCO +
one CUDEM region (CUDEM's −5.18 m winning over GEBCO's −6 m in-tile; z10–13 only
over the CUDEM footprint, no collisions). That validation **carried into the
rewrite** and the bash prototype was retired. The same property is now exercised
by `just preview` (GEBCO + CUDEM NY-harbor) and `just test-engine`.

---

## Phase 2 — European coverage: EMODnet + DDM 🟡 INGEST DONE, CUTOVER PENDING

**The goal:** replace the GEBCO/EMODnet/DDM bathymetry dropdown in
[openwatersio/seamap](https://github.com/openwatersio/seamap) with one unified,
self-hosted mosaic served by the Worker — dropping the maptoolkit.net CDN +
client-side maplibre-contour the seamap viewer uses today. The mosaic makes three
picks one: GEBCO base, EMODnet over European seas (z11), DDM over Danish waters
(z12), best-wins.

**Done:** `sources/emodnet/` (58-tile ERDDAP file_list → the source stage's
multi-file download handles it) and `sources/ddm/` (single GeoTIFF, EPSG:3034,
`--negate` for positive-down depth, `--crs`). Both prepare in CI's per-source
matrix and feed the aggregation merge.

**Remaining**

- **Global GEBCO planet build at scale.** Seamap is global, so the base must be
  built planet-wide. The four-stage pipeline + R2-backed incremental rebuild is
  the mechanism ([SCALING.md](SCALING.md)); a full planet run is the gate.
- **Seam check** where EMODnet/DDM meet GEBCO — confirm the LAT/MSL datum offsets
  don't seam at native zoom; the merge's Gaussian feather hides the visual seam.
  Constant offset + feather for now; VDatum (Phase 5) only where a seam shows.
- **Seamap viewer cutover** (in the *seamap* repo, not here): point its raster-dem
  + vector-contour layers at the Worker endpoint, drop the dropdown +
  client-side contours. This is the actual ship.
- **CI R2 mirror** of EMODnet/ERDDAP + DDM/SDFI so runners don't re-fetch each
  build (the per-source cache + R2 store already exist; confirm the heavy sources
  are mirrored).

**Validation:** a European preview (Danish + adjacent waters so DDM, EMODnet,
GEBCO all appear) — DDM wins over EMODnet wins over GEBCO; offsets don't seam;
spot-check depths.

---

## Phase 3 — US coverage: CUDEM + BlueTopo 🟡 CUDEM INGEST DONE

**CUDEM is now one unified `cudem` source** (replacing the old per-window
`cudem_ne`/`cudem_puget`). Its `file_list.txt` points at NOAA's **manifest**
(`urllist8483.txt`) rather than data files; `source_register_remote.py` reads each
tile's header and registers it as a `/vsicurl/` reference (see [Source ingest](#source-ingest-vsicurl-streaming-over-public-buckets))
— **no download**; aggregation range-reads the COGs straight off NOAA. Confirmed by
direct S3 inspection of `s3://noaa-nos-coastal-lidar-pds/dem/NCEI_ninth_Topobathy_2014_8483/`:

- **942 tiles, ~188 GB, 1/9 arc-second (~3.4 m)** — the only CUDEM resolution that
  integrates bathy **and** topo. 0.25° tile grid, NAVD88 vertical / NAD83
  (EPSG:4269) horizontal, NoData −9999. The index/manifest was regenerated
  2026-04-21, so the catalog is current (tiles are the 2014 CONUS epoch; newer
  fidelity lives in scattered per-project `CoNED_*`/`NGS_*Topobathy*` dirs → own
  sources, Phase 5).
- ⚠️ **The master `.vrt` is incomplete** — `…_EPSG-4269.vrt` (339 tiles) and
  `…_1.vrt` (591 tiles) are complementary *halves*, and the master alone omits the
  entire Southeast, Texas, Chesapeake, AL/NW-FL, and New England. `urllist8483.txt`
  (942 tiles) is the **only complete enumeration** — hence pointing the source at
  it, not the VRT.

**18 regional subdirs collapse to 4 coasts** (the natural grouping):

| Group    | Subdirs |
| -------- | ------- |
| Atlantic | northeast_sandy, MA_NH_ME, chesapeake_bay, NC, southeast, rima, FL(atl) |
| Gulf     | TX, LA_MS, AL_nwFL, FL(gulf) |
| Pacific  | CA, OR, columbia_river, wash_pugetsound, wash_outercoast, wash_juandefuca, wash_bellingham |
| Alaska   | AK |

Kept as **one source** for now: every tile shares resolution (→ z13), datum,
provider, and processing, so splitting buys nothing for priority/merge — it's
purely operational (CI cache granularity, per-overlay `.pmtiles` size). Splitting
later is just partitioning the manifest into per-coast `file_list.txt`s — trivial
when a real constraint forces it.

**Remaining**

- ✅ **Disk reality → solved by `/vsicurl/` streaming.** 188 GB won't fit a standard
  GitHub runner (~14 GB). Resolved by the [`/vsicurl/` streaming](#source-ingest-vsicurl-streaming-over-public-buckets)
  model: CUDEM is registered as `/vsicurl/` NOAA references (no download) and the
  aggregate job no longer syncs `store/source`, so the 188 GB never lands. Validated
  on the harbor preview; CI confirmation pending.
- **BlueTopo (new format work):** per-tile UTM zones (cross-zone mosaic), a
  GeoPackage tile index, 3-band rasters, **MLLW** chart datum (real offset; VDatum
  in Phase 5). `s3://noaa-ocs-nationalbathymetry-pds/`. Likely a new
  `source_download_*` variant + a metadata band-select knob.
- **Optional CUDEM extensions:** the **1/3 arc-sec** product (`NCEI_third_Topobathy_2014_8580`,
  ~10 m, broader/cheaper) as a coarse fill where 1/9 is absent; the **territory**
  products (Hawaii, PuertoRico, USVI, Guam, AmSam, CNMI — each its own
  `NCEI_ninth_Topobathy_*` manifest) as additional `cudem_*` sources reusing the
  same `source_download_filelist` step.

**Validation:** a multi-region US build mosaics without gaps (esp. across the
master-VRT-omitted Southeast/Gulf); total bundle size stays sane (sparse high-zoom
over coasts only); depths match known soundings.

---

## Phase 4 — Unify GEBCO as just another source ✅ DONE (free)

The rewrite delivered this: GEBCO is `sources/gebco/` (source #0), tiled by the
same `source → aggregation` path as every other source; the base/region
special-case from the old `build` is gone, and swapping the base (GEBCO→ETOPO) or
running base-less is a source-dir change. The old "abrupt z9→z10" worry is also
gone — the planet cap tiles every source *downsampled into the merged base*, so
the cap-zoom tile inside a regional footprint already shows that source.

---

## Phase 5 — Fidelity & ops (ongoing, as needed)

- **Proper VDatum vertical transforms** replacing constant offsets, where Phase
  2–3 seams prove inadequate. The seam is isolated in `source_datum.py` — swap the
  constant for a spatially-varying separation grid in that one step.
- **GEBCO TID-based quality masking** — prefer measured cells over interpolated
  when blending (would also feed a per-pixel provenance band off the merge).
- **Source-footprint provenance layer** — tile straight from the coverage polygons
  the source stage already generates; "which source covers here," free, anytime.
- **NOAA CSB** crowdsourced fill; **GLOBathy** lakes (separate inland layer).
- **Auto-refresh** as sources update (GEBCO annual, others irregular).

Pull these in only when a concrete need appears — most users won't notice a
constant offset vs full VDatum at these zooms.

---

## What does *not* change from the original intent

- One terrain tileset + one contour tileset, GEBCO everywhere + regional detail
  where data supports it. (Now served *through the Worker* as planet + overlays,
  not a single merged file per layer.)
- Adding a source is config + a recipe, never engine surgery — now a `sources/<id>/`
  dir (`metadata.json` + `file_list.txt` + `Justfile`) instead of a `sources.conf`
  row.
- The hard, open-ended work (datum normalization, format adapters, seams) stays
  isolated in the source stage (`source_datum.py`, `source_download_*.py`) and
  deferred to the sources that need it.

## Effort summary

| Phase | Scope                              | Status |
| ----- | ---------------------------------- | ------ |
| 0     | GEBCO base                         | ✅ done |
| 1     | Abstraction + 1-region proof       | ✅ done (rewritten) |
| 2     | European: EMODnet + DDM (seamap)   | 🟡 ingest done; planet build + seamap cutover left |
| 3     | US: CUDEM full + BlueTopo          | 🟡 CUDEM unified source done; CI disk strategy + BlueTopo left |
| 4     | Unify GEBCO as a source            | ✅ done (free) |
| 5     | VDatum, TID, provenance, CSB, lakes| ongoing |
