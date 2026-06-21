/* Shared Leaflet/OSM map component, reused by the Fetch and Visualize views. */
const FlightMap = (function () {
  let map = null;
  let trackLayer = null;
  let runwayLayer = null;
  let markerLayer = null;
  let replayLayer = null;

  function init() {
    if (map) return map;
    // No tile basemap: a solid dark-blue canvas (set in CSS) with thin
    // state-boundary outlines only — minimal, no map detail.
    // preferCanvas: render the many tracks/segments on canvas for performance.
    map = L.map("map", { zoomControl: true, attributionControl: false, preferCanvas: true })
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
      color: "#6f8cbd",
      weight: 1,
      opacity: 0.7,
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
    replayLayer = L.layerGroup().addTo(map); // trail + plane, topmost
    loadRunways();
    loadDetail();
    return map;
  }

  function clear() {
    if (trackLayer) trackLayer.clearLayers();
    if (runwayLayer) runwayLayer.clearLayers();
    if (markerLayer) markerLayer.clearLayers();
    if (replayLayer) replayLayer.clearLayers();
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

  // ---------------------------------------------------------------------------
  // Visualize view: all cached flights, hover tooltips, selection, color schemes,
  // and a plane-icon replay. Separate from the live Fetch rendering above.
  // ---------------------------------------------------------------------------
  let vizItems = []; // [{id, feature, count}]
  let vizAirports = {};
  let flightLayers = {}; // id -> {visible:[layers], feature, count, selected}
  let colorScheme = "route";
  let selectedId = null;
  let onSelectCb = null;
  let replay = null;
  let replaying = false;

  // Toggle pointer events on the vector canvas so tracks don't react to hover
  // (highlight/tooltip) while a replay is playing.
  function setTracksInteractive(on) {
    if (!map) return;
    const cv = map.getPane("overlayPane").querySelector("canvas");
    if (cv) cv.style.pointerEvents = on ? "" : "none";
  }
  function closeAllTooltips() {
    Object.values(flightLayers).forEach((fl) =>
      fl.visible.forEach((ly) => ly.closeTooltip && ly.closeTooltip())
    );
  }

  function segmentsOf(geom) {
    return geom.type === "LineString" ? [geom.coordinates] : geom.coordinates;
  }
  function flatCoords(geom) {
    return geom.type === "LineString" ? geom.coordinates : geom.coordinates.flat();
  }
  function tooltipText(p) {
    return `${p.date} · ${p.flight} · ${p.from}→${p.diverted_to || p.to}`;
  }
  function altColor(altFt) {
    const t = Math.max(0, Math.min(1, (altFt || 0) / 40000));
    return `hsl(${Math.round(240 * t)}, 90%, 55%)`; // 0=red (low) -> 240=blue (high)
  }
  function setBase(ly, w, o) { ly._bw = w; ly._bo = o; ly.setStyle({ weight: w, opacity: o }); }
  function restore(ly) { ly.setStyle({ weight: ly._bw, opacity: ly._bo }); }
  function emphasize(ly) { ly.setStyle({ weight: ly._bw + 2, opacity: 1 }); ly.bringToFront(); }

  function hoverFlight(id, on) {
    if (replaying) return; // no hover highlight/tooltip during replay
    const fl = flightLayers[id];
    if (!fl) return;
    const hot = on || fl.selected;
    fl.visible.forEach((ly) => (hot ? emphasize(ly) : restore(ly)));
  }

  function buildFlight(item) {
    const grp = L.layerGroup();
    const visible = [];
    const tip = tooltipText(item.feature.properties);
    const bind = (ly) => {
      ly.bindTooltip(tip, { sticky: true, direction: "top", className: "flight-tip" });
      ly.on("mouseover", () => hoverFlight(item.id, true));
      ly.on("mouseout", () => hoverFlight(item.id, false));
      ly.on("click", () => onSelectCb && onSelectCb(item.id));
      visible.push(ly);
    };
    segmentsOf(item.feature.geometry).forEach((seg) => {
      const ll = seg.map((c) => [c[1], c[0]]);
      if (colorScheme === "altitude") {
        for (let i = 0; i < seg.length - 1; i++) {
          const alt = ((seg[i][2] || 0) + (seg[i + 1][2] || 0)) / 2;
          const ly = L.polyline([ll[i], ll[i + 1]], { color: altColor(alt), interactive: true });
          setBase(ly, 2.5, 0.95);
          ly.addTo(grp);
          bind(ly);
        }
      } else {
        const op = routeOpacity(item.count);
        L.polyline(ll, { color: TRACK_COLOR, weight: 7, opacity: 0.18 * op, interactive: false }).addTo(grp);
        const core = L.polyline(ll, { color: TRACK_COLOR, interactive: true });
        setBase(core, 2, op);
        core.addTo(grp);
        bind(core);
      }
    });
    return { group: grp, visible };
  }

  function renderTracks() {
    trackLayer.clearLayers();
    flightLayers = {};
    vizItems.forEach((item) => {
      const { group, visible } = buildFlight(item);
      group.addTo(trackLayer);
      flightLayers[item.id] = { visible, feature: item.feature, count: item.count, selected: false };
    });
    if (selectedId && flightLayers[selectedId]) {
      flightLayers[selectedId].selected = true;
      flightLayers[selectedId].visible.forEach(emphasize);
    }
  }

  /* Render all cached flights for the Visualize view. */
  function showFlights(items, airports, opts) {
    stopReplay();
    beginRun(); // clears layers + bounds + airport dedupe
    vizItems = items;
    vizAirports = airports || {};
    colorScheme = (opts && opts.colorBy) || "route";
    onSelectCb = (opts && opts.onSelect) || null;
    selectedId = null;

    renderTracks();
    Object.values(flightLayers).forEach((fl) =>
      fl.visible.forEach((ly) => runBounds.extend(ly.getBounds()))
    );
    Object.entries(vizAirports).forEach(([iata, a]) => addAirport({ iata, lat: a.lat, lon: a.lon }));
    if (runBounds.isValid()) map.fitBounds(runBounds, { padding: [50, 50] });
  }

  function setColorScheme(scheme) {
    if (scheme === colorScheme) return;
    colorScheme = scheme;
    stopReplay();
    renderTracks();
  }

  function selectFlight(id) {
    if (selectedId && flightLayers[selectedId]) {
      flightLayers[selectedId].selected = false;
      flightLayers[selectedId].visible.forEach(restore);
    }
    selectedId = id;
    const fl = flightLayers[id];
    if (fl) {
      fl.selected = true;
      fl.visible.forEach(emphasize);
    }
  }

  function bearing(a, b) {
    // a, b = [lon, lat]
    const toR = (d) => (d * Math.PI) / 180;
    const f1 = toR(a[1]), f2 = toR(b[1]), dl = toR(b[0] - a[0]);
    const y = Math.sin(dl) * Math.cos(f2);
    const x = Math.cos(f1) * Math.sin(f2) - Math.sin(f1) * Math.cos(f2) * Math.cos(dl);
    return (((Math.atan2(y, x) * 180) / Math.PI) + 360) % 360;
  }

  function planeIconHtml() {
    return (
      '<svg width="22" height="22" viewBox="0 0 24 24">' +
      '<path fill="currentColor" d="M12 2l1.6 7.2 7.4 3.6v1.8l-7.4-2.2L13 19l2.4 1.6v1.2L12 20.6 8.6 21.8v-1.2L11 19l-.6-4.8L3 16.4v-1.8l7.4-3.6z"/></svg>'
    );
  }
  function planeIcon() {
    return L.divIcon({ className: "plane-icon", iconSize: [22, 22], iconAnchor: [11, 11], html: planeIconHtml() });
  }
  function rotatePlane(marker, deg) {
    const el = marker.getElement();
    const svg = el && el.querySelector("svg");
    if (svg) svg.style.transform = `rotate(${deg}deg)`;
  }

  function dimAll(on) {
    Object.values(flightLayers).forEach((fl) =>
      fl.visible.forEach((ly) => (on ? ly.setStyle({ opacity: 0.08 }) : (fl.selected ? emphasize : restore)(ly)))
    );
  }

  /* Animate a plane flying along a flight's path with a growing trail. */
  function replayFlight(feature, opts) {
    stopReplay();
    const pts = flatCoords(feature.geometry);
    if (pts.length < 2) return null;
    const ll = pts.map((c) => [c[1], c[0]]);
    // Zoom to the flight so the plane is actually watchable.
    const fb = L.latLngBounds(ll);
    if (fb.isValid()) map.fitBounds(fb, { padding: [80, 80] });
    const dist = [0];
    for (let i = 1; i < ll.length; i++) dist.push(dist[i - 1] + map.distance(ll[i - 1], ll[i]));
    const total = dist[dist.length - 1] || 1;

    replaying = true;
    closeAllTooltips();
    setTracksInteractive(false);
    dimAll(true);
    const trail = L.polyline([ll[0]], { color: "#ffffff", weight: 3, opacity: 0.95 }).addTo(replayLayer);
    const plane = L.marker(ll[0], { icon: planeIcon(), interactive: false, keyboard: false }).addTo(replayLayer);
    rotatePlane(plane, bearing(pts[0], pts[1]));

    const BASE_MS = 12000; // 0.75x of the previous 9s base (slower default)
    let speed = (opts && opts.speed) || 1;
    let elapsed = 0, last = null, paused = false, raf = null;

    function frame(ts) {
      if (last == null) last = ts;
      if (!paused) elapsed += (ts - last) * speed;
      last = ts;
      const d = Math.min(total, (elapsed / BASE_MS) * total);
      let i = 1;
      while (i < dist.length && dist[i] < d) i++;
      const i0 = i - 1, i1 = Math.min(i, ll.length - 1);
      const seg = dist[i1] - dist[i0] || 1;
      const f = Math.max(0, Math.min(1, (d - dist[i0]) / seg));
      const lat = ll[i0][0] + (ll[i1][0] - ll[i0][0]) * f;
      const lon = ll[i0][1] + (ll[i1][1] - ll[i0][1]) * f;
      plane.setLatLng([lat, lon]);
      rotatePlane(plane, bearing(pts[i0], pts[i1]));
      trail.setLatLngs(ll.slice(0, i1).concat([[lat, lon]]));
      if (opts && opts.onTick) opts.onTick(d / total);
      if (d >= total) {
        stopReplay();
        if (opts && opts.onDone) opts.onDone();
        return;
      }
      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);

    replay = {
      pause() { paused = true; },
      resume() { paused = false; last = null; },
      isPaused() { return paused; },
      setSpeed(s) { speed = s; },
      _stop() { if (raf) cancelAnimationFrame(raf); },
    };
    return replay;
  }

  function stopReplay() {
    if (replay) { replay._stop(); replay = null; }
    if (replayLayer) replayLayer.clearLayers();
    if (Object.keys(flightLayers).length) dimAll(false);
    replaying = false;
    setTracksInteractive(true);
  }

  function refresh() {
    if (map) setTimeout(() => map.invalidateSize(), 0);
  }

  return {
    init, clear, beginRun, setRoutes, addFlights, refresh,
    showFlights, setColorScheme, selectFlight, replayFlight, stopReplay,
  };
})();

document.addEventListener("DOMContentLoaded", FlightMap.init);
