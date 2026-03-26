# GEBCO Bathymetry Tiles

Convert [GEBCO](https://www.gebco.net/) gridded bathymetry data into tiles for use with MapLibre GL:

- **Terrain-RGB raster tiles** — for depth shading (color-relief), hillshade, and 3D terrain
- **Vector contour tiles** — pre-generated bathymetric contour lines with non-uniform intervals

Part of the [OpenWaters](https://github.com/openwatersio) project — building modern open-source tools for marine navigation.

## Contributing

### Local (with dependencies installed)

```bash
# Install dependencies (macOS)
brew install gdal tippecanoe jq rio

# Or on Ubuntu/Debian
sudo apt install gdal-bin python3-gdal python3-numpy jq bc
# tippecanoe: build from https://github.com/felt/tippecanoe
# rio-rgbify: pip install rio-rgbify

# Run the full pipeline (downloads ~7.5 GB)
./scripts/pipeline.sh

# Or for a regional extract (much faster for testing)
BBOX="-85,20,-70,35" ./scripts/pipeline.sh
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

The workflow at `.github/workflows/ci.yml` can be triggered manually:

1. Go to **Actions** → **Build** → **Run workflow**
2. Optionally set a bounding box for a regional build
3. Tiles are deployed to GitHub Pages when complete

## Preview

Start the Vite dev server to preview the map locally:

```bash
npm install
npm run dev
```

This serves the web viewer at `http://localhost:5173` with the built tiles from `output/` as the public directory.

You can also drag any `.pmtiles` file into the [PMTiles Viewer](https://protomaps.github.io/PMTiles/) to inspect tile contents.

## Pipeline scripts

| Script                             | Purpose                                             |
| ---------------------------------- | --------------------------------------------------- |
| `scripts/pipeline.sh`              | Runs the full pipeline end to end                   |
| `scripts/download.sh`              | Downloads GEBCO GeoTIFF (with optional BBOX clip)   |
| `scripts/build-terrain-rgb.sh`     | Builds Terrain-RGB raster tiles (MBTiles + PMTiles) |
| `scripts/contour` | Generates contour vector tiles (PMTiles)            |
| `scripts/smooth-contours.py`       | Chaikin corner-cutting smoothing for contour lines  |
| `scripts/config.sh`                | Shared configuration (intervals, paths, helpers)    |

## Configuration

Set environment variables before running:

| Variable           | Default            | Description                                     |
| ------------------ | ------------------ | ----------------------------------------------- |
| `BBOX`             | _(empty = global)_ | Bounding box `"west,south,east,north"` (commas) |
| `MAX_ZOOM`         | `14`               | Maximum contour tile zoom level                 |
| `TERRAIN_MAX_ZOOM` | `9`                | Maximum terrain-RGB tile zoom level             |
| `THREADS`          | `nproc`            | Parallel processing threads                     |
| `FORCE`            | _(empty)_          | Set to `1` to rebuild all cached files          |
| `GEBCO_URL`        | GEBCO 2025         | Override the download URL                       |

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

Attribution: _GEBCO Compilation Group (2025) GEBCO 2025 Grid
(doi:10.5285/37c52e96-24ea-67ce-e063-7086abc05f29)_

## Prior Art

- https://github.com/versatiles-org/opendem-gebco-bathymetry/
- https://github.com/shiwaku/gebco-2025-grid-tile-on-maplibre
