import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Protocol } from "pmtiles";

// ─── PMTiles protocol ─────────────────────────────────────────────────────
const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

// ─── Tile sources ───────────────────────────────────────────────────────
// BBOX and TILES_BASE are injected at build time via vite.config.js.
// eslint-disable-next-line no-undef
const BBOX = __BBOX__ ? __BBOX__.split(",").map(Number) : [-180, -85, 180, 85];
const suffix = __BBOX__ ? `_${__BBOX__}` : "";
// eslint-disable-next-line no-undef
const tilesBase = __TILES_BASE__ || location.origin;
const terrainUrl = `pmtiles://${tilesBase}/terrain${suffix}.pmtiles`;
const contourUrl = `pmtiles://${tilesBase}/contours${suffix}.pmtiles`;

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
      url: terrainUrl,
      tileSize: 512,
      // maxzoom auto-detected from the PMTiles header so regional high-res bands render where present
      encoding: "mapbox",
      bounds: BBOX,
      attribution: "&copy; <a href='https://www.gebco.net'>GEBCO</a>",
    },
    contours: {
      type: "vector",
      url: contourUrl,
      bounds: BBOX,
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
        "hillshade-exaggeration": 0.6,
        "hillshade-shadow-color": "#000022",
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
  ],
};

// ─── Create map ───────────────────────────────────────────────────────────
const map = new maplibregl.Map({
  container: "map",
  style,
  bounds: BBOX,
  hash: true,
});

map.addControl(new maplibregl.NavigationControl());

// Enable terrain so queryTerrainElevation() can read from the DEM.
// exaggeration: 0 keeps the map visually flat.
map.on("load", () => {
  map.setTerrain({ source: "terrain-dem", exaggeration: 0.0001 });
});

// ─── Layer toggles ────────────────────────────────────────────────────────
const toggles = {
  "toggle-depth": ["depth-shading"],
  "toggle-hillshade": ["hillshade"],
  "toggle-contours": ["contour-lines"],
  "toggle-labels": ["contour-labels"],
};

map.on("load", () => {
  for (const [inputId, layerIds] of Object.entries(toggles)) {
    document.getElementById(inputId).addEventListener("change", (e) => {
      const vis = e.target.checked ? "visible" : "none";
      layerIds.forEach((id) => map.setLayoutProperty(id, "visibility", vis));
    });
  }
});

// ─── Click to inspect ─────────────────────────────────────────────────────
map.on("click", (e) => {
  // Read elevation from terrain-RGB DEM tiles
  const eleRaw = map.queryTerrainElevation(e.lngLat);
  if (eleRaw == null) return;

  // queryTerrainElevation returns elevation * exaggeration
  const exaggeration = map.getTerrain()?.exaggeration || 1;
  const ele = eleRaw / exaggeration;
  const depth = Math.round(-ele);
  const depthFt = Math.round(depth * 3.28084);
  const label =
    ele <= 0 ? `${depth}m (${depthFt}ft)` : `${Math.round(ele)}m elevation`;

  new maplibregl.Popup()
    .setLngLat(e.lngLat)
    .setHTML(`<strong>${label}</strong>`)
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
