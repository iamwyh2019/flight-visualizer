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
        sortKey: parseDate(p.date),
      };
    });
    items.sort((a, b) => b.sortKey - a.sortKey);
  }

  function getVisible() {
    return items.filter(
      (it) => (!fAirline || it.props.airline === fAirline) && (!fYear || it.year === Number(fYear))
    );
  }

  function populateFilters() {
    const airlines = [...new Set(items.map((it) => it.props.airline))].filter(Boolean).sort();
    const years = [...new Set(items.map((it) => it.year))].sort((a, b) => b - a);
    $("filter-airline").innerHTML =
      '<option value="">All airlines</option>' + airlines.map((a) => `<option value="${a}">${a}</option>`).join("");
    $("filter-year").innerHTML =
      '<option value="">All years</option>' + years.map((y) => `<option value="${y}">${y}</option>`).join("");
  }

  function applyFilter() {
    const vis = getVisible();
    if (selectedId && !vis.some((it) => it.id === selectedId)) {
      selectedId = null;
      stopReplay();
      $("replay-btn").disabled = true;
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
      row.innerHTML =
        `<span class="fl-date">${it.props.date}</span>` +
        `<span class="fl-num">${it.props.flight}</span>` +
        `<span class="fl-route">${it.props.from}→${it.props.diverted_to || it.props.to}</span>`;
      row.addEventListener("click", () => select(it.id, false));
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
    $("replay-btn").disabled = false;
  }

  function render() {
    FlightMap.setRoutes(data.routes, data.route_max);
    FlightMap.showFlights(getVisible(), data.airports, {
      colorBy: scheme,
      onSelect: (id) => select(id, true),
    });
    if (selectedId) FlightMap.selectFlight(selectedId);
  }

  function setReplayBtn(label) {
    $("replay-btn").innerHTML = label;
  }
  function stopReplay() {
    FlightMap.stopReplay();
    replaying = false;
    paused = false;
    replayCtl = null;
    setReplayBtn("▶ Replay selected");
  }
  function onReplayClick() {
    if (!selectedId) return;
    if (!replaying) {
      const fl = items.find((it) => it.id === selectedId);
      if (!fl) return;
      replayCtl = FlightMap.replayFlight(fl.feature, { speed, onDone: stopReplay });
      if (!replayCtl) return;
      replaying = true;
      paused = false;
      setReplayBtn("⏸ Pause");
    } else if (!paused) {
      replayCtl.pause();
      paused = true;
      setReplayBtn("▶ Resume");
    } else {
      replayCtl.resume();
      paused = false;
      setReplayBtn("⏸ Pause");
    }
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
    $("speed-toggle").querySelectorAll(".seg-btn").forEach((b) =>
      b.addEventListener("click", () => {
        speed = Number(b.dataset.speed);
        $("speed-toggle").querySelectorAll(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
        if (replayCtl) replayCtl.setSpeed(speed);
      })
    );
    $("replay-btn").addEventListener("click", onReplayClick);
    $("filter-airline").addEventListener("change", (e) => { fAirline = e.target.value; applyFilter(); });
    $("filter-year").addEventListener("change", (e) => { fYear = e.target.value; applyFilter(); });
  }

  let wired = false;
  function activate() {
    if (!wired) { wireControls(); wired = true; }
    if (!loaded) {
      $("flight-list").innerHTML = '<div class="fl-row"><span class="muted">Loading…</span></div>';
      fetch("/api/flights")
        .then((r) => r.json())
        .then((d) => {
          data = d;
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
