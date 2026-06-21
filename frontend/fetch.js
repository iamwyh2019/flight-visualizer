/* Fetch view: run the full by-day backfill and render its live SSE stream.
 * Each day streams back as it completes; the map fills in incrementally and the
 * raw archive for a day is deleted server-side before the next day starts. */
(function () {
  const btn = document.getElementById("fetch-btn");
  const stopBtn = document.getElementById("stop-btn");
  const logEl = document.getElementById("log");
  const statusLine = document.getElementById("status-line");
  const flightCard = document.getElementById("flight-card");

  let daysDone = 0;
  let flightsDrawn = 0;
  let es = null; // active EventSource
  let running = false;
  let paused = false;
  let pausing = false;
  let hovering = false;
  let curLabel = "Fetching…";
  let curDate = "";

  const SPINNER = '<span class="spinner"></span>';
  const plural = (n) => `${n} ${n === 1 ? "flight" : "flights"}`;
  const fmtGB = (b) => (b / 1e9).toFixed(2) + " GB";

  // The button's background gradient IS the progress bar; --pct sets the fill.
  function btnFill(pct) {
    btn.style.setProperty("--pct", Math.max(0, Math.min(100, pct)).toFixed(1) + "%");
  }
  // Single source of truth for the button label, driven by state + hover.
  function renderButton() {
    if (!running) { btn.textContent = "Fetch all flights"; return; }
    if (paused) { btn.innerHTML = "▶ Resume"; return; }
    if (pausing) { btn.innerHTML = SPINNER + "Pausing…"; return; }
    if (hovering) { btn.innerHTML = "⏸ Click to pause"; return; } // hover reveals the action
    btn.innerHTML = SPINNER + curLabel;
  }
  function setFetchingLabel(text) {
    curLabel = text || "Fetching…";
    renderButton();
  }
  function setStatus(text) {
    statusLine.classList.toggle("hidden", !text);
    statusLine.textContent = text || "";
  }

  function setRunning(on) {
    running = on;
    paused = false;
    pausing = false;
    btn.classList.toggle("running", on);
    stopBtn.classList.toggle("hidden", !on);
    if (on) {
      curLabel = "Fetching…";
      btnFill(0);
    } else {
      btn.style.removeProperty("--pct");
      setStatus("");
    }
    renderButton();
  }

  // Same button toggles start -> pause -> resume.
  function onFetchClick() {
    if (!running) startFetch();
    else if (paused) resumeFetch();
    else if (!pausing) pauseFetch();
  }

  function pauseFetch() {
    pausing = true;
    fetch("/api/pause", { method: "POST" });
    renderButton();
    setStatus("Pausing after the current day finishes…");
  }

  function resumeFetch() {
    fetch("/api/resume", { method: "POST" });
    paused = false;
    curLabel = "Resuming…";
    renderButton();
    setStatus("Resuming…");
  }

  function log(line) {
    logEl.textContent += line + "\n";
    logEl.scrollTop = logEl.scrollHeight;
  }

  function setHeader(totalDays) {
    flightCard.classList.remove("hidden");
    let head = flightCard.querySelector(".day-note");
    if (!head) {
      flightCard.innerHTML =
        `<div class="day-note"></div>` +
        `<table class="day-table">` +
        `<thead><tr><th>Date</th><th>Flights</th><th>Source</th></tr></thead>` +
        `<tbody id="day-rows"></tbody></table>`;
      head = flightCard.querySelector(".day-note");
    }
    head.textContent =
      `Backfill: ${daysDone}/${totalDays} days · ${plural(flightsDrawn)} drawn`;
  }

  function addDayRow(d) {
    const tbody = document.getElementById("day-rows");
    const tr = document.createElement("tr");
    const tag = d.summary.downloaded ? "downloaded" : "cached";
    if (d.summary.drawn === 0) tr.className = "empty-row";
    tr.innerHTML =
      `<td>${d.date}</td>` +
      `<td>${plural(d.summary.drawn)}</td>` +
      `<td><span class="src ${tag}">${tag}</span></td>`;
    tbody.prepend(tr); // newest on top
  }

  function startFetch() {
    if (running) return;
    logEl.textContent = "";
    daysDone = 0;
    flightsDrawn = 0;
    curDate = "";
    flightCard.classList.add("hidden");
    flightCard.innerHTML = "";
    FlightMap.beginRun();
    setRunning(true);
    setStatus("Starting…");

    es = new EventSource("/api/fetch-latest");

    es.addEventListener("log", (e) => log(JSON.parse(e.data).message));

    es.addEventListener("routes", (e) => {
      const d = JSON.parse(e.data);
      FlightMap.setRoutes(d.counts, d.max); // line opacity by route frequency
    });

    es.addEventListener("paused", () => {
      paused = true;
      pausing = false;
      renderButton(); // "▶ Resume"; fill stays frozen at current %
      setStatus("Paused — current day cached. Click Resume to continue.");
    });

    es.addEventListener("resumed", () => {
      paused = false;
      setFetchingLabel("Fetching…");
    });

    es.addEventListener("day_start", (e) => {
      const d = JSON.parse(e.data);
      curDate = d.date;
      setFetchingLabel(`Fetching… Day ${d.index}/${d.total}`);
      if (d.need_download) {
        btnFill(0);
        setStatus(`${d.date} — downloading ${plural(d.flights)}…`);
      } else {
        btnFill(100);
        setStatus(`${d.date} — cached`);
      }
    });

    es.addEventListener("progress", (e) => {
      const d = JSON.parse(e.data);
      if (d.total) {
        const pct = (d.downloaded / d.total) * 100;
        btnFill(pct);
        setStatus(`${curDate} — ${fmtGB(d.downloaded)} / ${fmtGB(d.total)} (${pct.toFixed(0)}%)`);
      }
    });

    es.addEventListener("day", (e) => {
      const d = JSON.parse(e.data);
      daysDone += 1;
      flightsDrawn += d.summary.drawn;
      FlightMap.addFlights(d.flights); // incremental map update
      setHeader(d.total);
      addDayRow(d);
      log(`✓ Day ${d.index}/${d.total} ${d.date}: ${plural(d.summary.drawn)} drawn`);
    });

    es.addEventListener("done", (e) => {
      const s = JSON.parse(e.data).summary;
      log(s.stopped ? "■ Stopped." : "✓ Backfill complete.");
      es.close();
      es = null;
      setRunning(false);
      setStatus(
        s.stopped
          ? `Stopped — ${plural(flightsDrawn)} drawn so far (resume anytime)`
          : `Done — ${plural(s.flights_drawn)} drawn` +
              (s.empty ? `, ${s.empty} with no ADS-B data` : "")
      );
    });

    es.addEventListener("error", (e) => {
      if (e.data) {
        try {
          log("✗ ERROR: " + JSON.parse(e.data).message);
        } catch (_) {}
      }
      if (es) {
        es.close();
        es = null;
      }
      setRunning(false);
      if (e.data) setStatus("Failed — see log");
    });
  }

  function stopFetch() {
    if (!running) return;
    // Closing the stream disconnects the client; the server aborts the backfill
    // and deletes the in-flight archive.
    if (es) {
      es.close();
      es = null;
    }
    log("■ Stopping… (finishing current chunk, then cleaning up)");
    setRunning(false);
    setStatus(`Stopped — ${plural(flightsDrawn)} drawn so far (resume anytime)`);
  }

  btn.addEventListener("click", onFetchClick);
  btn.addEventListener("mouseenter", () => { hovering = true; renderButton(); });
  btn.addEventListener("mouseleave", () => { hovering = false; renderButton(); });
  stopBtn.addEventListener("click", stopFetch);
})();
