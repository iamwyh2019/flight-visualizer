/* Visualize view controller: loads all cached flights, builds the flight list,
 * wires hover/click selection, color-scheme toggle, and plane-icon replay. */
const Visualize = (function () {
  let loaded = false;
  let data = null; // /api/flights response
  let items = []; // [{id, feature, count, props, sortKey}]
  let selectedId = null;
  let scheme = "route";
  let speed = 1;
  let fAirline = "";
  let fYear = "";
  let airlines = {}; // ICAO -> {name, iata, logo}

  const airlineName = (c) => (airlines[c] && airlines[c].name) || c;
  const airlineLogo = (c) => (airlines[c] && airlines[c].logo) || "";
  let replaying = false;
  let paused = false;
  let replayCtl = null;

  const $ = (id) => document.getElementById(id);

  function parseDate(s) {
    const [m, d, y] = (s || "").split("/").map(Number);
    return new Date(2000 + (y || 0), (m || 1) - 1, d || 1).getTime();
  }

  function buildItems() {
    items = data.flights.map((f) => {
      const p = f.properties;
      const key = [p.from, p.diverted_to || p.to].sort().join("|");
      return {
        id: [p.date, p.flight, p.tail_number].join("_"),
        feature: f,
        count: data.routes[key] || 1,
        props: p,
        year: 2000 + (parseInt((p.date || "").split("/")[2], 10) || 0),
        // Full takeoff timestamp when available so same-day flights order by time.
        sortKey: p.takeoff ? Date.parse(p.takeoff) : parseDate(p.date),
      };
    });
    items.sort((a, b) => b.sortKey - a.sortKey); // reverse chronological
  }

  function getVisible() {
    return items.filter(
      (it) => (!fAirline || it.props.airline === fAirline) && (!fYear || it.year === Number(fYear))
    );
  }

  function populateFilters() {
    const airlinesList = [...new Set(items.map((it) => it.props.airline))].filter(Boolean).sort();
    const years = [...new Set(items.map((it) => it.year))].sort((a, b) => b - a);
    $("filter-airline").innerHTML =
      '<option value="">All airlines</option>' +
      airlinesList.map((a) => `<option value="${a}">${airlineName(a)}</option>`).join("");
    $("filter-year").innerHTML =
      '<option value="">All years</option>' + years.map((y) => `<option value="${y}">${y}</option>`).join("");
  }

  function applyFilter() {
    const vis = getVisible();
    if (selectedId && !vis.some((it) => it.id === selectedId)) {
      selectedId = null;
      stopReplay();
    }
    renderList();
    render();
  }

  function renderList() {
    const list = $("flight-list");
    list.innerHTML = "";
    const vis = getVisible();
    vis.forEach((it) => {
      const row = document.createElement("div");
      row.className = "fl-row";
      row.dataset.id = it.id;
      const logo = airlineLogo(it.props.airline);
      const logoHtml = logo
        ? `<img class="fl-logo" src="${logo}" alt="" title="${airlineName(it.props.airline)}">`
        : `<span class="fl-logo ph"></span>`;
      row.innerHTML =
        logoHtml +
        `<span class="fl-date">${it.props.date}</span>` +
        `<span class="fl-num">${it.props.flight}</span>` +
        `<span class="fl-route">${it.props.from}→${it.props.diverted_to || it.props.to}</span>`;
      row.addEventListener("click", () => select(it.id, false));
      row.addEventListener("mouseenter", () => FlightMap.highlight(it.id, true));
      row.addEventListener("mouseleave", () => FlightMap.highlight(it.id, false));
      list.appendChild(row);
    });
    const total = items.length;
    $("flight-count").textContent = vis.length === total ? `(${total})` : `(${vis.length}/${total})`;
  }

  function select(id, fromMap) {
    if (replaying) stopReplay();
    selectedId = id;
    FlightMap.selectFlight(id);
    document.querySelectorAll(".fl-row").forEach((r) =>
      r.classList.toggle("selected", r.dataset.id === id)
    );
    if (fromMap) {
      // ids contain '/' (from the date) but no quotes, so a quoted attr selector is safe.
      const row = document.querySelector(`.fl-row[data-id="${id}"]`);
      if (row) row.scrollIntoView({ block: "nearest" });
    }
    replayIdleLabel();
  }

  function render() {
    FlightMap.setRoutes(data.routes, data.route_max);
    FlightMap.showFlights(getVisible(), data.airports, {
      colorBy: scheme,
      onSelect: (id) => select(id, true),
    });
    if (selectedId) FlightMap.selectFlight(selectedId);
    replayIdleLabel();
  }

  // Idle state of the left "Replay" button.
  function replayIdleLabel() {
    if (selectedId) {
      $("replay-btn").disabled = false;
      $("replay-btn").innerHTML = "▶ Replay selected";
    } else {
      $("replay-btn").disabled = true;
      $("replay-btn").innerHTML = "Select a flight to replay";
    }
  }

  // --- bottom replay player ---
  let t0 = 0, t1 = 0; // selected flight takeoff/landing (ms)
  const pad = (n) => String(n).padStart(2, "0");
  const fmtISO = (s) => (s && s.includes("T") ? s.split("T")[1].slice(0, 5) : "--:--");
  const fmtMs = (ms) => { const d = new Date(ms); return pad(d.getHours()) + ":" + pad(d.getMinutes()); };

  function updateProgress(frac) {
    $("rp-fill").style.width = (frac * 100).toFixed(1) + "%";
    $("rp-cur").textContent = t1 > t0 ? fmtMs(t0 + frac * (t1 - t0)) : fmtISO(null);
  }
  function onReplayEnd() { paused = true; $("rp-play").textContent = "▶"; }

  function startReplay() {
    if (!selectedId) return;
    const fl = items.find((it) => it.id === selectedId);
    if (!fl) return;
    const p = fl.props;
    t0 = p.takeoff ? Date.parse(p.takeoff) : 0;
    t1 = p.landing ? Date.parse(p.landing) : 0;
    $("rp-start").textContent = fmtISO(p.takeoff);
    $("rp-end").textContent = fmtISO(p.landing);
    $("rp-play").textContent = "⏸";
    updateProgress(0);
    $("replay-player").classList.remove("hidden");
    replayCtl = FlightMap.replayFlight(fl.feature, { speed, onTick: updateProgress, onEnd: onReplayEnd });
    if (!replayCtl) { $("replay-player").classList.add("hidden"); return; }
    replaying = true;
    paused = false;
  }
  function onReplayClick() { if (selectedId && !replaying) startReplay(); }

  function togglePlay() {
    if (!replayCtl) return;
    paused = !paused;
    if (paused) replayCtl.pause();
    else replayCtl.resume();
    $("rp-play").textContent = paused ? "▶" : "⏸";
  }
  function stopReplay() {
    FlightMap.stopReplay();
    replaying = false;
    paused = false;
    replayCtl = null;
    $("replay-player").classList.add("hidden");
    replayIdleLabel();
  }

  let dragging = false;
  function seekFromEvent(e) {
    if (!replayCtl) return;
    const r = $("rp-track").getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    replayCtl.seek(frac);
    updateProgress(frac);
  }

  function wireControls() {
    $("color-toggle").querySelectorAll(".seg-btn").forEach((b) =>
      b.addEventListener("click", () => {
        scheme = b.dataset.scheme;
        $("color-toggle").querySelectorAll(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
        $("alt-legend").classList.toggle("hidden", scheme !== "altitude");
        FlightMap.setColorScheme(scheme);
        if (selectedId) FlightMap.selectFlight(selectedId);
        stopReplay();
      })
    );
    $("rp-speed").querySelectorAll(".seg-btn").forEach((b) =>
      b.addEventListener("click", () => {
        speed = Number(b.dataset.speed);
        $("rp-speed").querySelectorAll(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
        if (replayCtl) replayCtl.setSpeed(speed);
      })
    );
    $("replay-btn").addEventListener("click", onReplayClick);
    $("rp-play").addEventListener("click", togglePlay);
    $("rp-close").addEventListener("click", stopReplay);
    // Scrubber: click or drag to seek.
    $("rp-track").addEventListener("mousedown", (e) => { dragging = true; seekFromEvent(e); });
    window.addEventListener("mousemove", (e) => { if (dragging) seekFromEvent(e); });
    window.addEventListener("mouseup", () => { dragging = false; });
    $("filter-airline").addEventListener("change", (e) => { fAirline = e.target.value; applyFilter(); });
    $("filter-year").addEventListener("change", (e) => { fYear = e.target.value; applyFilter(); });
  }

  let wired = false;
  function activate() {
    if (!wired) { wireControls(); wired = true; }
    if (!loaded) {
      $("flight-list").innerHTML = '<div class="fl-row"><span class="muted">Loading…</span></div>';
      // Live backend if present; otherwise the static export (no-backend deploy).
      const loadFlights = fetch("/api/flights")
        .then((r) => { if (!r.ok) throw new Error("no api"); return r.json(); })
        .catch(() => fetch("data/flights.json").then((r) => r.json()));
      Promise.all([
        loadFlights,
        fetch("data/airlines.json").then((r) => r.json()).catch(() => ({})),
      ])
        .then(([d, al]) => {
          data = d;
          airlines = al || {};
          loaded = true;
          buildItems();
          populateFilters();
          renderList();
          render();
        })
        .catch(() => {
          $("flight-list").innerHTML = '<div class="fl-row"><span class="muted">Failed to load flights.</span></div>';
        });
    } else {
      render(); // re-draw (the shared map may have been used by Fetch)
    }
  }

  function deactivate() {
    stopReplay();
  }

  return { activate, deactivate };
})();
