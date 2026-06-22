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
  // Flight time (elapsed duration), not wall-clock — avoids cross-timezone confusion.
  const fmtDur = (ms) => {
    const m = Math.max(0, Math.round(ms / 60000));
    const h = Math.floor(m / 60);
    return h ? h + "h " + pad(m % 60) + "m" : m + "m";
  };

  function updateProgress(frac) {
    $("rp-fill").style.width = (frac * 100).toFixed(1) + "%";
    $("rp-cur").textContent = t1 > t0 ? fmtDur(frac * (t1 - t0)) : "--";
    drawChart(frac);
  }
  function onReplayEnd() { paused = true; $("rp-play").textContent = "▶"; }

  // --- altitude-vs-progress line chart (top of the player) ---
  let chartProfile = null;
  const fmtFt = (ft) => Math.round(ft).toLocaleString() + " ft";

  function altAt(frac) {
    const { x, alt } = chartProfile;
    if (frac <= 0) return alt[0];
    if (frac >= 1) return alt[alt.length - 1];
    let i = 1;
    while (i < x.length && x[i] < frac) i++;
    const t = (frac - x[i - 1]) / ((x[i] - x[i - 1]) || 1);
    return alt[i - 1] + (alt[i] - alt[i - 1]) * t;
  }

  function drawChart(frac) {
    const cv = $("rp-chart");
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
    }
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!chartProfile) return;

    const padL = 4, padR = 4, padT = 14, padB = 4;
    const plotW = w - padL - padR, plotH = h - padT - padB;
    const peak = Math.max(1000, ...chartProfile.alt);
    const maxAlt = Math.ceil(peak / 10000) * 10000; // nice round ceiling for ticks
    const X = (t) => padL + t * plotW;
    const Y = (a) => padT + plotH - (a / maxAlt) * plotH;

    // Horizontal grid + altitude labels every 10k ft.
    ctx.font = "10px ui-monospace, monospace";
    ctx.textBaseline = "bottom";
    for (let a = 0; a <= maxAlt; a += 10000) {
      const y = Y(a);
      ctx.strokeStyle = "rgba(125, 211, 252, 0.12)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + plotW, y);
      ctx.stroke();
      if (a > 0) {
        ctx.fillStyle = "rgba(148, 163, 184, 0.7)";
        ctx.fillText(a / 1000 + "k", padL + 2, y - 1);
      }
    }

    // Altitude profile: filled area + bright line.
    ctx.beginPath();
    chartProfile.x.forEach((t, i) => {
      const px = X(t), py = Y(chartProfile.alt[i]);
      i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    });
    ctx.lineTo(X(1), Y(0));
    ctx.lineTo(X(0), Y(0));
    ctx.closePath();
    ctx.fillStyle = "rgba(56, 189, 248, 0.14)";
    ctx.fill();

    ctx.beginPath();
    chartProfile.x.forEach((t, i) => {
      const px = X(t), py = Y(chartProfile.alt[i]);
      i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    });
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "round";
    ctx.stroke();

    // Cursor at the current progress + a dot on the curve and an altitude readout.
    const cx = X(Math.max(0, Math.min(1, frac)));
    const cy = Y(altAt(frac));
    ctx.strokeStyle = "rgba(255, 255, 255, 0.5)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(cx, padT);
    ctx.lineTo(cx, padT + plotH);
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.beginPath();
    ctx.arc(cx, cy, 3, 0, Math.PI * 2);
    ctx.fill();

    const label = fmtFt(altAt(frac));
    ctx.font = "11px ui-monospace, monospace";
    ctx.textBaseline = "top";
    const tw = ctx.measureText(label).width;
    ctx.fillStyle = "#7dd3fc";
    ctx.textAlign = "left";
    ctx.fillText(label, Math.min(cx + 4, padL + plotW - tw), 1);
  }

  function startReplay() {
    if (!selectedId) return;
    const fl = items.find((it) => it.id === selectedId);
    if (!fl) return;
    const p = fl.props;
    t0 = p.takeoff ? Date.parse(p.takeoff) : 0;
    t1 = p.landing ? Date.parse(p.landing) : 0;
    $("rp-start").textContent = "0m";
    $("rp-end").textContent = t1 > t0 ? fmtDur(t1 - t0) : "--";
    $("rp-play").textContent = "⏸";
    chartProfile = FlightMap.altitudeProfile(fl.feature);
    $("replay-player").classList.remove("hidden");
    updateProgress(0); // after un-hiding so the canvas has a measurable width
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
    chartProfile = null;
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
