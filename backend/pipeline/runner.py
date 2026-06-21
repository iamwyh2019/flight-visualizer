"""Orchestrate the fetch pipeline, emitting progress events.

`emit(kind, **fields)` is the sink for live updates; the web layer runs this in a
worker thread and forwards events to the browser over SSE. Event kinds:
  - log        : free-text status line
  - routes     : {counts, max} — per-route flight counts (drives line opacity)
  - day_start  : {index, total, date, flights, need_download} — a group begins
  - progress   : {downloaded, total} — byte progress of the current download
  - day        : {index, total, date, flights:[entry...], summary} — a group finished
  - done       : {summary} — the whole run finished
  - error      : {message}

Flights are fetched a whole UTC day at a time (same-day dedup: one download + one
tar scan per day), keyed by the takeoff's UTC date. Two real failure modes are
tolerated gracefully:
  * Missing prod archive: some days' prod release is empty (a known adsb.lol gap);
    we fall back to the staging release (handled in github_archive).
  * Genuine coverage gaps: an aircraft is in the archive but has no points during
    the flight's window. These are remembered (negative cache) so we never
    re-download a multi-GB archive for a flight that will never resolve.
"""

from __future__ import annotations

import time
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
    flight: Flight
    icao: str
    dep: Airport
    arr: Airport
    start_utc: datetime
    end_utc: datetime
    archive_day: date = None  # the takeoff's UTC date

    @property
    def key(self) -> str:
        return cache.flight_key(self.flight)


def _flights_word(n: int) -> str:
    return "flight" if n == 1 else "flights"


def _route_key(a: str, b: str) -> str:
    """Unordered route identity, e.g. ORD<->MSN and MSN<->ORD share one key."""
    return "|".join(sorted([a, b]))


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


def _plan(flight: Flight, emit: EmitFn, cache_dir: str | Path) -> _Plan | None:
    """Resolve icao + airports + UTC window + candidate archive days, or None."""
    try:
        icao = reg_to_icao(flight.tail_number, cache_dir)
        arrival_iata = flight.diverted_to or flight.to_iata
        dep = get_airport(flight.from_iata)
        arr = get_airport(arrival_iata)
        start_utc = local_to_utc(flight.takeoff_actual, flight.from_iata)
        end_utc = local_to_utc(flight.landing_actual, arrival_iata)
    except NotImplementedError as exc:
        emit("log", message=f"Skipping {flight.flight} ({flight.tail_number}): {exc}")
        return None
    except KeyError as exc:
        emit("log", message=f"Skipping {flight.flight} ({flight.tail_number}): {exc}")
        return None

    return _Plan(flight, icao, dep, arr, start_utc, end_utc, start_utc.date())


def _slice_to_feature(p: _Plan, raw: bytes, cache_dir: str | Path, emit: EmitFn) -> dict | None:
    """Decode a trace, slice this flight's leg, build + cache the feature (or None)."""
    trace = trace_extract.decode_trace(raw)
    trace_extract.verify_registration(trace, p.flight.tail_number, emit)
    segments = slice_clean.process(trace, p.start_utc, p.end_utc)
    if not segments:
        return None
    feature = build_feature(p.flight, segments)
    cache.save_feature(cache_dir, p.flight, feature)
    return feature


def fetch_all_days(
    csv_path: str | Path,
    work_dir: str | Path,
    emit: EmitFn,
    cache_dir: str | Path,
    cancelled: Callable[[], bool] = lambda: False,
    paused: Callable[[], bool] = lambda: False,
) -> dict:
    """Process every flight in the log, day by day, streaming results to the map."""
    _clean_tmp(work_dir)

    flyable = [
        f
        for f in parse_csv(csv_path)
        if not f.canceled and f.takeoff_actual and f.landing_actual and f.tail_number
    ]
    plans = [p for p in (_plan(f, emit, cache_dir) for f in flyable) if p is not None]
    if not plans:
        raise ValueError("No processable (US) flights found in CSV.")

    # Per-route counts up front (drives line opacity on the frontend).
    route_counts: dict[str, int] = defaultdict(int)
    for p in plans:
        route_counts[_route_key(p.dep.iata, p.arr.iata)] += 1
    route_max = max(route_counts.values()) if route_counts else 1
    emit("routes", counts=dict(route_counts), max=route_max)
    busiest = max(route_counts, key=route_counts.get) if route_counts else None
    if busiest:
        emit("log", message=f"Busiest route {busiest.replace('|', '–')} flown {route_max}x")

    empty = cache.load_empty(cache_dir)
    satisfied: set[str] = set()
    new_empty: set[str] = set()

    # Split into already-cached vs needs-fetching (skipping known-empty flights).
    cached_by_day: dict[date, list[dict]] = defaultdict(list)
    pending: list[_Plan] = []
    for p in plans:
        feat = cache.load_feature(cache_dir, p.flight)
        if feat is not None:
            cached_by_day[p.archive_day].append(_flight_entry(feat, p.dep, p.arr))
            satisfied.add(p.key)
        elif p.key in empty:
            emit("log", message=f"{p.flight.flight}: no data on record, skipping (cached miss)")
        else:
            pending.append(p)

    # Total day-groups for the progress label: cached days + distinct days to fetch.
    fetch_days = {p.archive_day for p in pending}
    total = len(set(cached_by_day) | fetch_days)
    idx = 0

    def emit_group(day: date, entries: list[dict], downloaded: bool, from_cache: int):
        nonlocal idx
        idx += 1
        n = len(entries)
        emit(
            "day",
            index=idx,
            total=total,
            date=day.isoformat(),
            flights=entries,
            summary={
                "date": day.isoformat(),
                "drawn": n,
                "from_cache": from_cache,
                "downloaded": downloaded,
            },
        )
        emit("log", message=f"✓ {day.isoformat()}: {n} {_flights_word(n)} drawn")

    # 1) Draw cached flights immediately (no downloads), newest day first.
    for day in sorted(cached_by_day, reverse=True):
        emit_group(day, cached_by_day[day], downloaded=False, from_cache=len(cached_by_day[day]))

    # 2) Download each needed UTC day once (newest first) and slice its flights.
    groups: dict[date, list[_Plan]] = defaultdict(list)
    for p in pending:
        groups[p.archive_day].append(p)

    for day in sorted(groups, reverse=True):
        if cancelled():
            emit("log", message="Run cancelled — stopping.")
            cache.save_empty(cache_dir, empty | new_empty)
            emit("done", summary=_summary(plans, satisfied, new_empty, stopped=True))
            return _summary(plans, satisfied, new_empty, stopped=True)

        # Pause point (between days): the previous day is already cached, so nothing
        # is wasted while we wait here for resume.
        if paused():
            emit("paused")
            while paused() and not cancelled():
                time.sleep(0.3)
            emit("resumed")
            if cancelled():
                emit("log", message="Run cancelled — stopping.")
                cache.save_empty(cache_dir, empty | new_empty)
                emit("done", summary=_summary(plans, satisfied, new_empty, stopped=True))
                return _summary(plans, satisfied, new_empty, stopped=True)

        day_plans = groups[day]
        icaos = sorted({p.icao for p in day_plans})
        emit("day_start", index=idx + 1, total=total, date=day.isoformat(),
             flights=len(day_plans), need_download=True)
        emit("log", message=f"{day.isoformat()}: downloading once for {len(icaos)} aircraft")
        try:
            traces = github_archive.fetch_traces(day, icaos, work_dir, emit, cancelled)
        except github_archive.Cancelled:
            emit("log", message="Run cancelled mid-download — archive cleaned up, stopping.")
            cache.save_empty(cache_dir, empty | new_empty)
            emit("done", summary=_summary(plans, satisfied, new_empty, stopped=True))
            return _summary(plans, satisfied, new_empty, stopped=True)
        except FileNotFoundError as exc:
            emit("log", message=f"{day.isoformat()}: {exc}")
            traces = {}

        entries: list[dict] = []
        for p in day_plans:
            if p.icao not in traces:
                continue
            feature = _slice_to_feature(p, traces[p.icao], cache_dir, emit)
            if feature is None:
                continue  # aircraft present but no points in window -> genuine gap
            satisfied.add(p.key)
            entries.append(_flight_entry(feature, p.dep, p.arr))
        emit_group(day, entries, downloaded=True, from_cache=0)

    # 3) Anything still unsatisfied after all candidate days -> negative cache.
    for p in pending:
        if p.key not in satisfied:
            new_empty.add(p.key)
            emit("log", message=f"{p.flight.flight}: no ADS-B data found, recording as empty")
    cache.save_empty(cache_dir, empty | new_empty)

    summary = _summary(plans, satisfied, new_empty, stopped=False)
    emit("done", summary=summary)
    return summary


def _summary(plans, satisfied, new_empty, stopped) -> dict:
    return {
        "days": len({p.archive_day for p in plans}),
        "flights_drawn": len(satisfied),
        "empty": len(new_empty),
        "skipped": sorted(new_empty),
        "stopped": stopped,
    }


def _clean_tmp(work_dir: str | Path) -> None:
    """Remove any orphaned archive parts left by a previously aborted run."""
    p = Path(work_dir)
    if p.exists():
        for f in p.glob("*.tar.*"):
            try:
                f.unlink()
            except OSError:
                pass
