import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

// ─── Tile sources ───────────────────────────────────────────────────────
// The serving Worker (worker/) presents one unified XYZ endpoint per layer:
//   {base}/{z}/{x}/{y}.webp   — Terrarium terrain raster (planet + overlays, overzoomed)
//   {base}/{z}/{x}/{y}.pbf    — MVT vector (contours, more layers later)
// VITE_TILES_BASE is the full bathymetry endpoint base — it includes the
// /seascape route prefix in prod (e.g. https://tiles.openwaters.io/seascape);
// the dev default points at the worker root. VITE_BBOX sets the initial view.
const BBOX = import.meta.env.VITE_BBOX
  ? import.meta.env.VITE_BBOX.split(",").map(Number)
  : [-180, -85, 180, 85];
const tilesBase = (
  import.meta.env.VITE_TILES_BASE || "http://localhost:8787"
).replace(/\/$/, "");
const terrainTiles = `${tilesBase}/{z}/{x}/{y}.webp`;
const contourTiles = `${tilesBase}/{z}/{x}/{y}.pbf`;
const MAX_ZOOM = 13; // deepest source; the Worker overzooms the base for the rest

// The Worker overzooms the raster terrain server-side up to MAX_ZOOM, but vector
// contours are a plain passthrough — so tell MapLibre their true max zoom (the
// deepest source in the manifest) and it overzooms them client-side above that.
const manifest = await fetch(`${tilesBase}/manifest.json`)
  .then((r) => r.json())
  .catch(() => null);
const contourMax = manifest
  ? Math.max(
      manifest.planet.max_zoom,
      ...manifest.sources.map((s) => s.max_zoom),
    )
  : MAX_ZOOM;

// ─── Source coverage (provenance) ───────────────────────────────────────────
// The `coverage` layer baked into contours.pmtiles (source footprints, props
// source_id / source_name / source_maxzoom). Drawn as polygons and queried on click to
// report which source a depth came from. Colour each source by id off the manifest list
// (a match expression); footprints of sources not in the manifest fall back to grey.
const COVERAGE_PALETTE = [
  "#e6194b", "#3cb44b", "#f032e6", "#4363d8", "#f58231",
  "#911eb4", "#008080", "#9a6324", "#800000", "#000075",
];
const coverageColor =
  manifest && manifest.sources.length
    ? [
        "match",
        ["get", "source_id"],
        ...manifest.sources.flatMap((s, i) => [
          s.id,
          COVERAGE_PALETTE[i % COVERAGE_PALETTE.length],
        ]),
        "#888",
      ]
    : "#f58231";

// ─── Map style ────────────────────────────────────────────────────────────
const style = {
  version: 8,
  name: "GEBCO Bathymetry",
  glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution:
        "&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a>",
    },
    "terrain-dem": {
      type: "raster-dem",
      tiles: [terrainTiles],
      tileSize: 512,
      maxzoom: MAX_ZOOM, // Worker overzooms the z8 base for z>8 where no overlay exists
      encoding: "terrarium",
      attribution: "&copy; <a href='https://www.gebco.net'>GEBCO</a>",
    },
    contours: {
      type: "vector",
      tiles: [contourTiles],
      maxzoom: contourMax,
    },
  },
  layers: [
    {
      id: "osm-base",
      type: "raster",
      source: "osm",
      paint: { "raster-opacity": 0.3 },
    },
    {
      id: "depth-shading",
      type: "color-relief",
      source: "terrain-dem",
      paint: {
        // Banded light-blue ramp ported from seamap's bathymetry-relief layer.
        "color-relief-color": [
          "interpolate",
          ["linear"],
          ["elevation"],
          -10000,
          "#bae7fe",
          -50.1,
          "#e9f7ff",
          -50,
          "#bae7fe",
          -20.1,
          "#bae7fe",
          -20,
          "#9adcfe",
          -10.1,
          "#9adcfe",
          -10,
          "#83d4fe",
          -5.1,
          "#83d4fe",
          -5,
          "#73cefe",
          -2.1,
          "#73cefe",
          -2,
          "#68cafe",
          -0.01,
          "#68cafe",
          // Land — transparent so the OSM base shows through (gebco-specific)
          0,
          "rgba(0, 0, 0, 0)",
        ],
        "color-relief-opacity": 0.85,
      },
    },
    {
      id: "hillshade",
      type: "hillshade",
      source: "terrain-dem",
      layout: { visibility: "none" },
      paint: {
        "hillshade-exaggeration": 0.5,
        "hillshade-shadow-color": "#9adcfe",
        "hillshade-highlight-color": "#ffffff",
        "hillshade-illumination-direction": 315,
      },
    },
    {
      id: "contour-lines",
      type: "line",
      source: "contours",
      "source-layer": "contours",
      paint: {
        "line-color": "#777",
        "line-width": 0.5,
        "line-opacity": 0.33,
      },
    },
    {
      id: "contour-labels",
      type: "symbol",
      source: "contours",
      "source-layer": "contours",
      filter: ["==", ["%", ["to-number", ["get", "depth_abs_m"]], 10], 0],
      minzoom: 8,
      layout: {
        "symbol-placement": "line",
        "text-field": ["concat", ["to-string", ["get", "depth_abs_m"]], "m"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 8, 8, 13, 10],
        "text-font": ["Open Sans Regular"],
        "text-letter-spacing": 0.1,
        "text-max-angle": 30,
        "text-padding": 50,
      },
      paint: {
        "text-color": "#777",
      },
    },
    {
      id: "source-fill",
      type: "fill",
      source: "contours",
      "source-layer": "coverage",
      // Hidden by default (the toggle starts unchecked); turning it on enables click-to-identify.
      layout: { visibility: "none" },
      paint: { "fill-color": coverageColor, "fill-opacity": 0.12 },
    },
    {
      // Brightened fill of the source a click landed in; filter set on click.
      id: "source-highlight",
      type: "fill",
      source: "contours",
      "source-layer": "coverage",
      filter: ["==", ["get", "source_id"], "__none__"],
      layout: { visibility: "none" },
      paint: { "fill-color": coverageColor, "fill-opacity": 0.4 },
    },
    {
      id: "source-outline",
      type: "line",
      source: "contours",
      "source-layer": "coverage",
      layout: { visibility: "none" },
      paint: { "line-color": coverageColor, "line-width": 1.5 },
    },
    {
      id: "source-labels",
      type: "symbol",
      source: "contours",
      "source-layer": "coverage",
      layout: {
        visibility: "none",
        "text-field": ["get", "source_name"],
        "text-size": 11,
        "text-font": ["Open Sans Regular"],
      },
      paint: {
        "text-color": coverageColor,
        "text-halo-color": "#fff",
        "text-halo-width": 1.2,
      },
    },
  ],
};

// ─── Create map ───────────────────────────────────────────────────────────
const map = new maplibregl.Map({
  container: "map",
  style,
  bounds: BBOX,
  hash: true,
});
window.map = map; // exposed for debugging / verification

map.addControl(new maplibregl.NavigationControl());

// No setTerrain(): enabling 3D terrain drapes the DEM layers (depth-shading, hillshade)
// over MapLibre's terrain mesh, which resamples the land+water DEM coarsely below native
// zoom. Near coasts the large positive land values bleed across the 0 m transparency
// cutoff, so narrow/shallow water (bays, sounds, shoals) renders transparent at z<8 —
// leaving only the deep open water, which then looks like coarse GEBCO. color-relief reads
// the tiles at native resolution instead. Click-depth reads the DEM tile directly
// (readElevation), so terrain isn't needed for it. Re-add behind a toggle for 3D seafloor.

// ─── Layer toggles ────────────────────────────────────────────────────────
const toggles = {
  "toggle-depth": ["depth-shading"],
  "toggle-hillshade": ["hillshade"],
  "toggle-contours": ["contour-lines"],
  "toggle-labels": ["contour-labels"],
  "toggle-sources": [
    "source-fill",
    "source-highlight",
    "source-outline",
    "source-labels",
  ],
};

map.on("load", () => {
  for (const [inputId, layerIds] of Object.entries(toggles)) {
    document.getElementById(inputId)?.addEventListener("change", (e) => {
      const vis = e.target.checked ? "visible" : "none";
      layerIds.forEach((id) => {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis);
      });
    });
  }
});

// ─── Click to inspect ─────────────────────────────────────────────────────
// Decode the elevation straight from the DEM tile pixel (Terrarium). This reads the tile
// at native resolution — unlike queryTerrainElevation, which needs 3D terrain enabled and
// samples the coarse terrain mesh (it reads land over deep water near the coast).
async function readElevation(lngLat) {
  const z = Math.min(Math.round(map.getZoom()), MAX_ZOOM);
  const n = 2 ** z;
  const fx = ((lngLat.lng + 180) / 360) * n;
  const fy =
    ((1 - Math.asinh(Math.tan((lngLat.lat * Math.PI) / 180)) / Math.PI) / 2) * n;
  const X = Math.floor(fx);
  const Y = Math.floor(fy);
  const px = Math.min(511, Math.floor((fx - X) * 512));
  const py = Math.min(511, Math.floor((fy - Y) * 512));
  const r = await fetch(`${tilesBase}/${z}/${X}/${Y}.webp`);
  if (!r.ok) return null;
  const bmp = await createImageBitmap(await r.blob());
  const cx = new OffscreenCanvas(512, 512).getContext("2d");
  cx.drawImage(bmp, 0, 0);
  const [r8, g8, b8] = cx.getImageData(px, py, 1, 1).data;
  return r8 * 256 + g8 + b8 / 256 - 32768; // Terrarium decode → metres
}

// Which source covers a clicked point: the deepest footprint wins (lex-first id on a
// tie), matching the build's merge rule, so this names the source the depth came from.
// undefined → coverage layer hidden (skip the line); null → no footprint here = GEBCO.
function sourceAt(point) {
  if (map.getLayoutProperty("source-fill", "visibility") === "none")
    return undefined;
  const hits = map.queryRenderedFeatures(point, { layers: ["source-fill"] });
  if (!hits.length) return null;
  return hits
    .map((f) => f.properties)
    .sort(
      (a, b) =>
        b.source_maxzoom - a.source_maxzoom ||
        (a.source_id < b.source_id ? -1 : 1),
    )[0];
}

map.on("click", async (e) => {
  const ele = await readElevation(e.lngLat);
  const src = sourceAt(e.point);
  if (map.getLayer("source-highlight"))
    map.setFilter("source-highlight", [
      "==",
      ["get", "source_id"],
      src?.source_id ?? "__none__",
    ]);

  const lines = [];
  if (ele != null) {
    const depth = Math.round(-ele);
    lines.push(
      `<strong>${
        ele <= 0
          ? `${depth}m (${Math.round(depth * 3.28084)}ft)`
          : `${Math.round(ele)}m elevation`
      }</strong>`,
    );
  }
  if (src !== undefined)
    lines.push(
      `<small>source: ${src ? src.source_name : "GEBCO (global)"}</small>`,
    );
  if (!lines.length) return;

  new maplibregl.Popup()
    .setLngLat(e.lngLat)
    .setHTML(lines.join("<br>"))
    .addTo(map);
});

map.on(
  "mouseenter",
  "contour-lines",
  () => (map.getCanvas().style.cursor = "pointer"),
);
map.on(
  "mouseleave",
  "contour-lines",
  () => (map.getCanvas().style.cursor = ""),
);
