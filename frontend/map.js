/* Shared Leaflet/OSM map component, reused by the Fetch and Visualize views. */
const FlightMap = (function () {
  let map = null;
  let trackLayer = null;
  let markerLayer = null;

  function init() {
    if (map) return map;
    // No tile basemap: a solid dark-blue canvas (set in CSS) with thin
    // state-boundary outlines only — minimal, no map detail.
    map = L.map("map", { zoomControl: true, attributionControl: false })
      .setView([39.5, -98.35], 4); // US center

    // State borders drawn underneath everything as a faint wireframe.
    fetch("data/us-states.geojson")
      .then((r) => r.json())
      .then((geo) => {
        L.geoJSON(geo, {
          style: {
            color: "#5b7bb0", // light slate-blue
            weight: 1,
            opacity: 0.5,
            fill: false,
          },
          interactive: false,
        }).addTo(map);
      })
      .catch(() => {/* borders are decorative; ignore load failures */});

    trackLayer = L.layerGroup().addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    return map;
  }

  function clear() {
    if (trackLayer) trackLayer.clearLayers();
    if (markerLayer) markerLayer.clearLayers();
  }

  function airportMarker(a, role) {
    const color = role === "from" ? "#22c55e" : "#ef4444";
    const icon = L.divIcon({
      className: "airport-pin",
      html: `<span style="background:${color}"></span>`,
      iconSize: [14, 14],
      iconAnchor: [7, 7],
    });
    return L.marker([a.lat, a.lon], { icon })
      .bindTooltip(`${a.iata} — ${a.name}`, { direction: "top" });
  }

  const PALETTE = ["#38bdf8", "#34d399", "#f472b6", "#fbbf24", "#a78bfa", "#fb7185"];

  function addTrack(feature, color, bounds) {
    // Glow: a wide, faint underlay beneath a bright core line.
    const glow = L.geoJSON(feature, { style: { color, weight: 9, opacity: 0.18 } });
    const core = L.geoJSON(feature, { style: { color, weight: 2.5, opacity: 1 } });
    trackLayer.addLayer(glow);
    trackLayer.addLayer(core);
    bounds.extend(core.getBounds());
  }

  /* Draw one or more flights (each {feature, airports}) with reference markers. */
  function drawFlights(flights) {
    init();
    clear();
    const bounds = L.latLngBounds([]);
    flights.forEach((fl, i) => {
      addTrack(fl.feature, PALETTE[i % PALETTE.length], bounds);
      if (fl.airports) {
        airportMarker(fl.airports.from, "from").addTo(markerLayer);
        airportMarker(fl.airports.to, "to").addTo(markerLayer);
        bounds.extend([fl.airports.from.lat, fl.airports.from.lon]);
        bounds.extend([fl.airports.to.lat, fl.airports.to.lon]);
      }
    });
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [50, 50] });
  }

  function refresh() {
    if (map) setTimeout(() => map.invalidateSize(), 0);
  }

  return { init, clear, drawFlights, refresh };
})();

document.addEventListener("DOMContentLoaded", FlightMap.init);
