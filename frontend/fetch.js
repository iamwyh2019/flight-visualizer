/* Fetch view: trigger the pipeline and render its live SSE stream. */
(function () {
  const btn = document.getElementById("fetch-btn");
  const logEl = document.getElementById("log");
  const progressWrap = document.getElementById("progress-wrap");
  const progressFill = document.getElementById("progress-fill");
  const progressText = document.getElementById("progress-text");
  const flightCard = document.getElementById("flight-card");

  function log(line) {
    logEl.textContent += line + "\n";
    logEl.scrollTop = logEl.scrollHeight;
  }

  function fmtGB(bytes) {
    return (bytes / 1e9).toFixed(2) + " GB";
  }

  const DOTS = ["#38bdf8", "#34d399", "#f472b6", "#fbbf24", "#a78bfa", "#fb7185"];

  function showDayCard(payload) {
    flightCard.classList.remove("hidden");
    const s = payload.summary;
    const rows = payload.flights
      .map((fl, i) => {
        const p = fl.feature.properties;
        const dot = DOTS[i % DOTS.length];
        return `<div class="flight-row">
          <span class="dot" style="background:${dot}"></span>
          <b>${p.from} → ${p.to}</b>
          <span>${p.flight}</span>
          <span>${p.aircraft_type}</span>
          <span>${fl.stats.points} pts</span>
        </div>`;
      })
      .join("");
    const note =
      `Day ${payload.date} — ${s.drawn} flight(s) drawn` +
      (s.downloaded ? `, ${s.from_cache} from cache + 1 download for the rest` : `, all from cache`) +
      (s.skipped.length ? `. Skipped: ${s.skipped.join(", ")}` : "");
    flightCard.innerHTML = `<div class="day-note">${note}</div>${rows}`;
  }

  btn.addEventListener("click", () => {
    btn.disabled = true;
    logEl.textContent = "";
    flightCard.classList.add("hidden");
    progressWrap.classList.remove("hidden");
    progressFill.style.width = "0%";
    progressText.textContent = "Starting…";

    const es = new EventSource("/api/fetch-latest");

    es.addEventListener("log", (e) => log(JSON.parse(e.data).message));

    es.addEventListener("progress", (e) => {
      const d = JSON.parse(e.data);
      if (d.total) {
        const pct = Math.min(100, (d.downloaded / d.total) * 100);
        progressFill.style.width = pct.toFixed(1) + "%";
        progressText.textContent =
          `Downloading ${fmtGB(d.downloaded)} / ${fmtGB(d.total)} (${pct.toFixed(0)}%)`;
      }
    });

    es.addEventListener("done", (e) => {
      const { payload } = JSON.parse(e.data);
      progressFill.style.width = "100%";
      progressText.textContent = "Done";
      log(`✓ Complete — drawing ${payload.flights.length} track(s).`);
      showDayCard(payload);
      FlightMap.drawFlights(payload.flights);
      es.close();
      btn.disabled = false;
    });

    es.addEventListener("error", (e) => {
      // SSE 'error' is fired both for our app errors (with data) and for
      // connection close. Only surface app errors that carry a message.
      if (e.data) {
        try {
          log("✗ ERROR: " + JSON.parse(e.data).message);
        } catch (_) {
          /* ignore */
        }
        progressText.textContent = "Failed";
      }
      es.close();
      btn.disabled = false;
    });
  });
})();
