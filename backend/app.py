"""FastAPI app: a local web GUI for the flight data fetcher.

Endpoints
- GET /api/fetch-latest : runs the single-flight pipeline, streaming live
  log/progress/done/error events to the browser as Server-Sent Events.
- /                     : serves the static frontend (Fetch + Visualize views).

The pipeline is blocking and runs in a worker thread; events are marshaled back
onto the event loop via call_soon_threadsafe and pushed to the browser as SSE.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .pipeline import runner
from .pipeline.airports import get_airport

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "flighty-logs"
FRONTEND_DIR = ROOT / "frontend"
WORK_DIR = ROOT / "data" / "tmp"
CACHE_DIR = ROOT / "data" / "cache"

app = FastAPI(title="Flight Visualizer — Data Fetcher")

# The pause flag for the currently-active backfill (single-user local tool).
_current_pause: threading.Event | None = None


def _latest_csv() -> Path:
    csvs = sorted(LOGS_DIR.glob("*.csv"))
    if not csvs:
        raise FileNotFoundError(f"No CSV found in {LOGS_DIR}")
    return csvs[-1]


def _format_sse(event: dict) -> str:
    kind = event.pop("kind")
    return f"event: {kind}\ndata: {json.dumps(event)}\n\n"


@app.post("/api/pause")
async def pause() -> dict:
    if _current_pause is not None:
        _current_pause.set()
    return {"ok": True}


@app.post("/api/resume")
async def resume() -> dict:
    if _current_pause is not None:
        _current_pause.clear()
    return {"ok": True}


@app.get("/api/flights")
async def flights() -> dict:
    """Aggregate every cached per-flight GeoJSON into one payload for the Visualize view.

    Reads the cache live (no on-disk combined file). Resolves the airport coords for
    every referenced IATA and computes per-route counts (for frequency-based opacity).
    """
    features: list[dict] = []
    for path in sorted((CACHE_DIR / "flights").glob("*.geojson")):
        try:
            features.append(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue

    airports: dict[str, dict] = {}
    route_counts: dict[str, int] = {}
    for feat in features:
        props = feat.get("properties", {})
        dep, arr = props.get("from"), props.get("diverted_to") or props.get("to")
        for code in (props.get("from"), props.get("to"), props.get("diverted_to")):
            if code and code not in airports:
                try:
                    a = get_airport(code)
                    airports[code] = {"lat": a.lat, "lon": a.lon, "name": a.name}
                except KeyError:
                    pass
        if dep and arr:
            key = "|".join(sorted([dep, arr]))
            route_counts[key] = route_counts.get(key, 0) + 1

    route_max = max(route_counts.values()) if route_counts else 1
    return {
        "flights": features,
        "airports": airports,
        "routes": route_counts,
        "route_max": route_max,
    }


@app.get("/api/fetch-latest")
async def fetch_latest() -> StreamingResponse:
    global _current_pause
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop = threading.Event()  # set when the client disconnects -> aborts the backfill
    pause = threading.Event()  # toggled by /api/pause and /api/resume
    _current_pause = pause

    def emit(kind: str, **fields) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"kind": kind, **fields})

    def work() -> None:
        try:
            csv_path = _latest_csv()
            emit("log", message=f"Using CSV {csv_path.name}")
            runner.fetch_all_days(
                csv_path, WORK_DIR, emit, CACHE_DIR,
                cancelled=stop.is_set, paused=pause.is_set,
            )
        except Exception as exc:  # surface any failure to the UI
            emit("error", message=f"{type(exc).__name__}: {exc}")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)  # completion sentinel

    async def event_stream():
        loop.run_in_executor(None, work)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _format_sse(event)
        finally:
            # Client went away (or stream ended): tell the worker to stop so it
            # doesn't keep downloading multi-GB archives in the background.
            stop.set()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Static frontend (mounted last so /api/* takes precedence).
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
