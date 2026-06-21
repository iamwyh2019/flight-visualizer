"""Orchestrate the fetch pipeline, emitting progress events.

`emit(kind, **fields)` is the sink for live updates (kinds: "log", "progress",
"done", "error"). This is deliberately synchronous/blocking; the web layer runs it
in a worker thread and forwards events to the browser over SSE.

Flights are processed a whole UTC day at a time: every flight on the same day
shares ONE archive download and ONE tar scan (same-day dedup), so N same-day
flights cost one download instead of N.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from . import cache, github_archive, slice_clean, trace_extract
from .airports import Airport, get_airport, local_to_utc
from .csv_parser import Flight, parse_csv
from .geojson_writer import build_feature
from .reg_to_icao import reg_to_icao

EmitFn = Callable[..., None]


@dataclass
class _Plan:
    """Everything needed to slice one flight out of its day's archive."""

    flight: Flight
    icao: str
    dep: Airport
    arr: Airport
    start_utc: datetime
    end_utc: datetime
    archive_date: date


def _count(feature: dict) -> tuple[int, int]:
    geom = feature["geometry"]
    if geom["type"] == "LineString":
        return len(geom["coordinates"]), 1
    return sum(len(seg) for seg in geom["coordinates"]), len(geom["coordinates"])


def _flight_entry(feature: dict, dep: Airport, arr: Airport) -> dict:
    n_pts, n_seg = _count(feature)
    return {
        "feature": feature,
        "airports": {
            "from": {"iata": dep.iata, "name": dep.name, "lat": dep.lat, "lon": dep.lon},
            "to": {"iata": arr.iata, "name": arr.name, "lat": arr.lat, "lon": arr.lon},
        },
        "stats": {"points": n_pts, "segments": n_seg},
    }


def _plan(flight: Flight, emit: EmitFn) -> _Plan | None:
    """Resolve icao + airports + UTC window for a flight, or None if unprocessable."""
    try:
        icao = reg_to_icao(flight.tail_number)
        arrival_iata = flight.diverted_to or flight.to_iata
        dep = get_airport(flight.from_iata)
        arr = get_airport(arrival_iata)
        start_utc = local_to_utc(flight.takeoff_actual, flight.from_iata)
        end_utc = local_to_utc(flight.landing_actual, arrival_iata)
        return _Plan(flight, icao, dep, arr, start_utc, end_utc, start_utc.date())
    except (NotImplementedError, KeyError) as exc:
        emit("log", message=f"Skipping {flight.flight} ({flight.tail_number}): {exc}")
        return None


def fetch_latest_day(
    csv_path: str | Path, work_dir: str | Path, emit: EmitFn, cache_dir: str | Path
) -> dict:
    """Process every flight on the most-recent UTC day in one download + scan."""
    flyable = [
        f
        for f in parse_csv(csv_path)
        if not f.canceled and f.takeoff_actual and f.landing_actual and f.tail_number
    ]
    if not flyable:
        raise ValueError("No flyable flights found in CSV.")

    # Group by UTC archive date and pick the latest day.
    plans = [p for p in (_plan(f, emit) for f in flyable) if p is not None]
    by_day: dict[date, list[_Plan]] = defaultdict(list)
    for p in plans:
        by_day[p.archive_date].append(p)
    target_day = max(by_day)
    day_plans = by_day[target_day]
    emit(
        "log",
        message=(
            f"Latest archive day {target_day.isoformat()} has {len(day_plans)} flight(s): "
            + ", ".join(f"{p.flight.flight} {p.flight.from_iata}->{p.flight.to_iata}" for p in day_plans)
        ),
    )

    # Reuse anything already cached; only the rest needs the archive.
    cached: dict[str, dict] = {}  # flight_key -> feature
    uncached: list[_Plan] = []
    for p in day_plans:
        feat = cache.load_feature(cache_dir, p.flight)
        if feat is not None:
            cached[cache.flight_key(p.flight)] = feat
        else:
            uncached.append(p)
    emit("log", message=f"{len(cached)} cached, {len(uncached)} need the archive")

    # One download for the whole day; extract every aircraft in a single scan.
    traces: dict[str, bytes] = {}
    if uncached:
        icaos = sorted({p.icao for p in day_plans})  # refresh all in the one scan
        emit("log", message=f"Downloading {target_day.isoformat()} once for {len(icaos)} aircraft")
        traces = github_archive.fetch_traces(target_day, icaos, work_dir, emit)

    # Slice each flight's leg out of the shared day trace.
    entries: list[dict] = []
    skipped: list[str] = []
    for p in day_plans:
        key = cache.flight_key(p.flight)
        feature = None
        if key in cached:
            feature = cached[key]
            emit("log", message=f"{p.flight.flight}: from cache")
        elif p.icao in traces:
            trace = trace_extract.decode_trace(traces[p.icao])
            trace_extract.verify_registration(trace, p.flight.tail_number, emit)
            segments = slice_clean.process(trace, p.start_utc, p.end_utc)
            if not segments:
                emit("log", message=f"{p.flight.flight}: no points in UTC window, skipped")
                skipped.append(p.flight.flight)
                continue
            feature = build_feature(p.flight, segments)
            saved = cache.save_feature(cache_dir, p.flight, feature)
            n = sum(len(s) for s in segments)
            emit("log", message=f"{p.flight.flight}: sliced {n} pts -> saved {saved.name}")
        else:
            emit("log", message=f"{p.flight.flight}: aircraft {p.icao} not in archive, skipped")
            skipped.append(p.flight.flight)
            continue
        entries.append(_flight_entry(feature, p.dep, p.arr))

    if not entries:
        raise ValueError(f"No tracks could be built for {target_day.isoformat()}.")

    payload = {
        "date": target_day.isoformat(),
        "flights": entries,
        "summary": {
            "total": len(day_plans),
            "drawn": len(entries),
            "from_cache": len(cached),
            "downloaded": bool(uncached),
            "skipped": skipped,
        },
    }
    emit("done", payload=payload)
    return payload
