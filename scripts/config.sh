#!/usr/bin/env bash
# Shared configuration for the GEBCO → vector tile pipeline.
# Source this from other scripts: source "$(dirname "$0")/config.sh"

set -euo pipefail

# ─── Paths ────────────────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${PROJECT_DIR}/data"
WORK_DIR="${PROJECT_DIR}/work"
OUTPUT_DIR="${PROJECT_DIR}/output"

mkdir -p "${DATA_DIR}" "${WORK_DIR}" "${OUTPUT_DIR}"

# ─── GEBCO source ─────────────────────────────────────────────────────────────
# GEBCO 2025 GeoTIFF — full global grid.
# Download from https://www.gebco.net/data-products-gridded-bathymetry-data/gebco2025-grid
# or use the download script. The file is ~7.5 GB uncompressed.
GEBCO_URL="${GEBCO_URL:-https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2025/ice_surface_elevation/geotiff/gebco_2025_geotiff.zip}"
GEBCO_ZIP="${DATA_DIR}/gebco_2025.zip"
GEBCO_TIF="${DATA_DIR}/gebco_2025.tif"

# ─── Bounding box (optional, for regional extracts) ──────────────────────────
# Format: "west,south,east,north"
# Leave empty for full global processing.
# Examples:
#   Bahamas:        BBOX="-85,20,-70,35"
#   US East Coast:  BBOX="-82,24,-65,45"
#   Mediterranean:  BBOX="-6,30,36,46"
BBOX="${BBOX:-}"

# ─── Contour levels ──────────────────────────────────────────────────────────
# Non-uniform: fine intervals for shallow water, coarse for deep.
# Must be in increasing order (most negative first) for gdal_contour -fl.
CONTOUR_LEVELS="-10000 -8000 -6000 -5000 -4000 -3000 -2000 -1500 -1000 -500 -200 -150 -100 -75 -50 -45 -40 -35 -30 -25 -20 -15 -14 -13 -12 -11 -10 -9 -8 -7 -6 -5 -4 -3 -2 -1"

# ─── Terrain RGB ──────────────────────────────────────────────────────────────
# GEBCO is 15 arc-second (~450m at equator). Zoom 9 ≈ 305m/pixel — a good
# match for the native resolution. MapLibre color-relief and maplibre-contour
# handle overzooming smoothly on the GPU / in web workers.
TERRAIN_MAX_ZOOM="${TERRAIN_MAX_ZOOM:-9}"

# ─── Processing ──────────────────────────────────────────────────────────────
# Set FORCE=1 to rebuild all intermediate and output files from scratch.
FORCE="${FORCE:-}"

THREADS="${THREADS:-$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# Tippecanoe max zoom for the output tileset.
MAX_ZOOM="${MAX_ZOOM:-14}"

# ─── Helpers ─────────────────────────────────────────────────────────────────
log() { echo "[$(date '+%H:%M:%S')] $*" >&2; }

# Returns 0 (true) if the file exists and FORCE is not set.
cached() { [[ -z "${FORCE}" && -e "$1" ]]; }

check_deps() {
  local missing=()
  for cmd in "$@"; do
    if ! command -v "$cmd" &>/dev/null; then
      missing+=("$cmd")
    fi
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: Missing required tools: ${missing[*]}"
    echo "Install them or use the project Dockerfile."
    exit 1
  fi
}

# Resolve the input DEM from an argument or the default clipped file.
# Usage: resolve_input_dem "$1"
# Sets INPUT_TIF and SUFFIX as globals.
resolve_input_dem() {
  INPUT_TIF="${1:-}"
  if [[ -z "${INPUT_TIF}" ]]; then
    local clipped="${WORK_DIR}/gebco_clipped${BBOX:+_${BBOX}}.tif"
    if [[ -f "${clipped}" ]]; then
      INPUT_TIF="${clipped}"
    else
      log "ERROR: No input DEM found. Run ./scripts/download first."
      exit 1
    fi
  fi
  SUFFIX="${BBOX:+_${BBOX}}"
  log "Input DEM: ${INPUT_TIF}"
}

# Upsample a DEM 2x with cubicspline resampling.
# Creates a C2-smooth surface through pixel centers, eliminating grid
# stairstepping in derived products (contours, depth shading).
# Usage: upsample_dem input.tif output.tif
upsample_dem() {
  local input="$1" output="$2"
  if cached "${output}"; then
    log "Upsampled DEM already exists"
    return
  fi
  rm -f "${output}"
  log "Upsampling DEM 2x with cubicspline..."
  local res
  res=$(gdalinfo -json "${input}" | jq -r '(.geoTransform[1,5] | fabs / 2) | tostring' | tr '\n' ' ')
  gdalwarp \
    -tr ${res} \
    -r cubicspline \
    -wo NUM_THREADS=ALL_CPUS \
    -overwrite \
    "${input}" \
    "${output}"
  log "  → ${output}"
}
