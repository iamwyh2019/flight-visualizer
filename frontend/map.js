/* Shared Leaflet/OSM map component, reused by the Fetch and Visualize views. */
const FlightMap = (function () {
  let map = null;
  let trackLayer = null;
  let runwayLayer = null;
  let markerLayer = null;

  function init() {
    if (map) return map;
    // No tile basemap: a solid dark-blue canvas (set in CSS) with thin
    // state-boundary outlines only — minimal, no map detail.
    map = L.map("map", { zoomControl: true, attributionControl: false })
      .setView([39.5, -98.35], 4); // US center

    // Borders drawn underneath everything as a faint wireframe: world country
    // outlines (so international airports aren't floating in a void) plus finer
    // US state outlines on top.
    const addBorders = (url, style) =>
      fetch(url)
        .then((r) => r.json())
        .then((geo) => L.geoJSON(geo, { style, interactive: false }).addTo(map))
        .catch(() => {/* borders are decorative; ignore load failures */});
    addBorders("data/world-countries.geojson", {
      color: "#3f567d",
      weight: 0.8,
      opacity: 0.45,
      fill: false,
    });
    addBorders("data/us-states.geojson", {
      color: "#5b7bb0",
      weight: 1,
      opacity: 0.5,
      fill: false,
    });

    trackLayer = L.layerGroup().addTo(map);
    runwayLayer = L.layerGroup().addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    loadRunways();
    loadDetail();
    return map;
  }

  function clear() {
    if (trackLayer) trackLayer.clearLayers();
    if (runwayLayer) runwayLayer.clearLayers();
    if (markerLayer) markerLayer.clearLayers();
  }

  // Airports are drawn once each (deduped by IATA) as glowing nodes with a label,
  // matching the cyan wireframe art style instead of bright from/to pins. When you
  // zoom in, each airport's real runway layout (a true-to-scale "bird's-eye"
  // wireframe) becomes visible — see runways.json + drawRunways().
  let seenAirports = new Set();
  let drawnAirports = []; // this run's airports, replayed once airport data loads
  let runwaysDrawnFor = new Set();
  let detailDrawnFor = new Set();
  let RUNWAYS = null; // { IATA: [ {le:[lat,lon], he:[lat,lon], width_ft} ] }
  let DETAIL = null; // { IATA: GeoJSON FeatureCollection (taxiway/apron/terminal) }

  function loadRunways() {
    if (RUNWAYS !== null) return;
    fetch("data/runways.json")
      .then((r) => r.json())
      .then((d) => {
        RUNWAYS = d;
        drawnAirports.forEach(drawRunways); // catch airports added before load
      })
      .catch(() => {
        RUNWAYS = {};
      });
  }

  function loadDetail() {
    if (DETAIL !== null) return;
    fetch("data/airport-detail.json")
      .then((r) => r.json())
      .then((d) => {
        DETAIL = d;
        drawnAirports.forEach(drawDetail);
      })
      .catch(() => {
        DETAIL = {};
      });
  }

  // Taxiways / aprons / terminals styled in the cyan wireframe family.
  function detailStyle(feature) {
    const k = feature.properties.kind;
    if (k === "taxiway") return { color: "#58a6cf", weight: 0.8, opacity: 0.5 };
    if (k === "apron")
      return { color: "#3a6088", weight: 0.4, opacity: 0.35, fillColor: "#16324e", fillOpacity: 0.22 };
    // terminal — brighter so buildings stand out
    return { color: "#9fdcff", weight: 1, opacity: 0.9, fillColor: "#7dd3fc", fillOpacity: 0.3 };
  }

  function drawDetail(a) {
    if (!DETAIL || detailDrawnFor.has(a.iata)) return;
    const fc = DETAIL[a.iata];
    if (!fc) return;
    detailDrawnFor.add(a.iata);
    L.geoJSON(fc, { style: detailStyle, interactive: false }).addTo(runwayLayer);
  }

  function airportNode(a) {
    const icon = L.divIcon({
      className: "airport-node",
      html: "<span></span>",
      iconSize: [12, 12],
      iconAnchor: [6, 6],
    });
    return L.marker([a.lat, a.lon], { icon, keyboard: false })
      .bindTooltip(a.iata, {
        permanent: true,
        direction: "right",
        offset: [6, 0],
        className: "airport-label",
      });
  }

  /* Build a true-to-scale rectangle (4 latlng corners) for one runway from its
     two centerline endpoints and width, by offsetting perpendicular to the line. */
  function runwayCorners(le, he, widthFt) {
    const half = (widthFt * 0.3048) / 2; // meters
    const latMid = ((le[0] + he[0]) / 2) * (Math.PI / 180);
    const mLat = 111320;
    const mLon = 111320 * Math.cos(latMid);
    const dxE = (he[1] - le[1]) * mLon; // east (m)
    const dyN = (he[0] - le[0]) * mLat; // north (m)
    const len = Math.hypot(dxE, dyN) || 1;
    const pE = -dyN / len; // perpendicular unit, east comp
    const pN = dxE / len; //               north comp
    const off = (lat, lon, s) => [lat + (pN * half * s) / mLat, lon + (pE * half * s) / mLon];
    return [off(le[0], le[1], 1), off(he[0], he[1], 1), off(he[0], he[1], -1), off(le[0], le[1], -1)];
  }

  function drawRunways(a) {
    if (!RUNWAYS || runwaysDrawnFor.has(a.iata)) return;
    const rwys = RUNWAYS[a.iata];
    if (!rwys) return;
    runwaysDrawnFor.add(a.iata);
    rwys.forEach((rw) => {
      L.polygon(runwayCorners(rw.le, rw.he, rw.width_ft), {
        color: "#7dd3fc",
        weight: 1,
        opacity: 0.9,
        fillColor: "#38bdf8",
        fillOpacity: 0.18,
        interactive: false,
      }).addTo(runwayLayer);
    });
  }

  function addAirport(a) {
    if (seenAirports.has(a.iata)) return;
    seenAirports.add(a.iata);
    airportNode(a).addTo(markerLayer);
    drawnAirports.push(a);
    drawRunways(a);
    drawDetail(a);
    runBounds.extend([a.lat, a.lon]);
  }

  // One uniform glowing color for every track (a "flight footprint" look that
  // scales to any number of flights, unlike per-flight colors).
  const TRACK_COLOR = "#38bdf8";
  let runBounds = L.latLngBounds([]); // accumulates across a whole run

  // Route frequency -> opacity: routes flown often glow bright, one-offs fade.
  const MIN_OPACITY = 0.6; // a one-off route
  const MAX_OPACITY = 1.0; // the most-flown route
  let routeCounts = {};
  let routeMax = 1;

  function setRoutes(counts, max) {
    routeCounts = counts || {};
    routeMax = max || 1;
  }

  function routeKey(airports) {
    return [airports.from.iata, airports.to.iata].sort().join("|");
  }

  function routeOpacity(count) {
    if (routeMax <= 1) return MAX_OPACITY;
    const t = (count - 1) / (routeMax - 1); // 0..1
    return MIN_OPACITY + (MAX_OPACITY - MIN_OPACITY) * Math.sqrt(t); // sqrt: lift mid counts
  }

  function addTrack(feature, opacity) {
    // Glow: a wide, faint underlay beneath a bright core line.
    const glow = L.geoJSON(feature, {
      style: { color: TRACK_COLOR, weight: 7, opacity: 0.18 * opacity },
    });
    const core = L.geoJSON(feature, {
      style: { color: TRACK_COLOR, weight: 2, opacity: opacity },
    });
    trackLayer.addLayer(glow);
    trackLayer.addLayer(core);
    runBounds.extend(core.getBounds());
  }

  /* Start a fresh run: wipe the map and reset accumulated bounds. */
  function beginRun() {
    init();
    clear();
    runBounds = L.latLngBounds([]);
    seenAirports = new Set();
    drawnAirports = [];
    runwaysDrawnFor = new Set();
    detailDrawnFor = new Set();
  }

  /* Append flights (each {feature, airports}) without clearing; refit to all so far. */
  function addFlights(flights) {
    init();
    flights.forEach((fl) => {
      const count = fl.airports ? routeCounts[routeKey(fl.airports)] || 1 : 1;
      addTrack(fl.feature, routeOpacity(count));
      if (fl.airports) {
        addAirport(fl.airports.from);
        addAirport(fl.airports.to);
      }
    });
    if (runBounds.isValid()) map.fitBounds(runBounds, { padding: [50, 50] });
  }

  function refresh() {
    if (map) setTimeout(() => map.invalidateSize(), 0);
  }

  return { init, clear, beginRun, setRoutes, addFlights, refresh };
})();

document.addEventListener("DOMContentLoaded", FlightMap.init);
