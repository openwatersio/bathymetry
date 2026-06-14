# GEBCO Bathymetry Tiles

Convert [GEBCO](https://www.gebco.net/) gridded bathymetry data into tiles for use with MapLibre GL:

- **Terrain-RGB raster tiles** — for depth shading (color-relief), hillshade, and 3D terrain
- **Vector contour tiles** — pre-generated bathymetric contour lines with non-uniform intervals

Part of the [OpenWaters](https://github.com/openwatersio) project — building modern open-source tools for marine navigation.

## Contributing

### Local (with dependencies installed)

```bash
# Install dependencies (macOS)
brew install gdal tippecanoe pmtiles jq
pip install rio-rgbify rasterio scipy

# Or on Ubuntu/Debian
sudo apt install gdal-bin python3-gdal python3-numpy python3-scipy python3-rasterio jq bc
pip install rio-rgbify
# tippecanoe: build from https://github.com/felt/tippecanoe
# pmtiles:    https://github.com/protomaps/go-pmtiles/releases

# Run the full global pipeline (downloads ~4.2 GB)
./scripts/build

# Or for a regional extract (much faster for testing)
BBOX="-85,20,-70,35" ./scripts/build
```

### Docker

```bash
docker build -t gebco-tiles .

# Full global build
docker run --rm -v $(pwd)/data:/app/data -v $(pwd)/output:/app/output gebco-tiles

# Regional extract (Bahamas)
docker run --rm -e BBOX="-85,20,-70,35" \
  -v $(pwd)/data:/app/data -v $(pwd)/output:/app/output gebco-tiles
```

### GitHub Actions

The workflow at `.github/workflows/ci.yml`:

- **On every push** it builds `terrain` and `contour` tiles as separate parallel
  jobs and saves them as downloadable workflow artifacts (the viewer builds too).
- **On a published release** it additionally pushes the tiles to Cloudflare R2
  (served at `tiles.openwaters.io`) and deploys the viewer to GitHub Pages.
- **Manual runs** (Actions → Build → Run workflow) accept an optional bounding box
  for a regional build; the default is full global.

Publishing requires these repository secrets: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.

## Preview

Start the Vite dev server to preview the map locally:

```bash
npm install
npm run dev
```

This serves the web viewer at `http://localhost:5173` with the built tiles from `output/` as the public directory.

You can also drag any `.pmtiles` file into the [PMTiles Viewer](https://protomaps.github.io/PMTiles/) to inspect tile contents.

## Pipeline scripts

| Script                   | Purpose                                                  |
| ------------------------ | -------------------------------------------------------- |
| `scripts/build`          | Runs the full pipeline end to end (download → tiles)     |
| `scripts/download`       | Downloads GEBCO GeoTIFF (with optional BBOX clip)        |
| `scripts/terrain`        | Builds Terrain-RGB raster tiles (PMTiles)                |
| `scripts/contour`        | Builds contour vector tiles (PMTiles)                    |
| `scripts/smooth-dem`     | Slope-selective DEM smoothing (flat areas only)          |
| `scripts/smooth-contours`| Chaikin corner-cutting smoothing for contour lines       |
| `scripts/config.sh`      | Shared configuration (intervals, paths, helpers)         |

## Configuration

Set environment variables before running:

| Variable           | Default            | Description                                     |
| ------------------ | ------------------ | ----------------------------------------------- |
| `BBOX`             | _(empty = global)_ | Bounding box `"west,south,east,north"` (commas) |
| `MAX_ZOOM`         | `9`                | Maximum contour tile zoom level                 |
| `TERRAIN_MAX_ZOOM` | `9`                | Maximum terrain-RGB tile zoom level             |
| `THREADS`          | `nproc`            | Parallel processing threads                     |
| `FORCE`            | _(empty)_          | Set to `1` to rebuild all cached files          |
| `GEBCO_YEAR`       | `2026`             | GEBCO grid release year (drives the source URL) |

## Data sources

This pipeline uses [GEBCO](https://www.gebco.net/) (15 arc-second global grid).
For higher resolution in specific regions, you can substitute or merge with:

- **NOAA BlueTopo** (2-16m, US waters) — `s3://noaa-bathymetry-pds/BlueTopo/`
- **EMODnet** (~3.6-115m, European waters)
- **NOAA CUDEM** (~3-10m, US coast)

Use `gdalwarp` or `gdalbuildvrt` to merge higher-res regional data with GEBCO
before running the contour generation step.

See [RESEARCH.md](./RESEARCH.md) for the full research summary.

## License

Scripts: MIT. Output data inherits GEBCO's terms (public domain, attribution required).

Attribution: _GEBCO Bathymetric Compilation Group 2026 (2026) The GEBCO_2026 Grid
(doi:10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa)_

## Prior Art

- https://github.com/versatiles-org/opendem-gebco-bathymetry/
- https://github.com/shiwaku/gebco-2025-grid-tile-on-maplibre
