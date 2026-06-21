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

from .pipeline import collect, runner

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
    """Aggregate every cached per-flight GeoJSON into the Visualize payload (live)."""
    return collect.build_flights_payload(CACHE_DIR)


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
