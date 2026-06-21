"""Persistent per-flight cache of the derived GeoJSON.

A completed historical flight's track never changes, so the derived data is
cached permanently (no expiry). This is the only thing worth keeping from a run:
the raw multi-GB archive is deleted, but the tiny per-flight feature (a few KB) is
saved so we never re-download a day we've already processed.

Cache key (per spec): tail_number + date + flight, filename-sanitized.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .csv_parser import Flight


def _sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s.strip())


def flight_key(flight: Flight) -> str:
    return _sanitize(f"{flight.date}_{flight.flight}_{flight.tail_number}")


def cache_path(cache_dir: str | Path, flight: Flight) -> Path:
    return Path(cache_dir) / "flights" / f"{flight_key(flight)}.geojson"


def load_feature(cache_dir: str | Path, flight: Flight) -> dict | None:
    path = cache_path(cache_dir, flight)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_feature(cache_dir: str | Path, flight: Flight, feature: dict) -> Path:
    path = cache_path(cache_dir, flight)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feature), encoding="utf-8")
    return path


# --- Negative cache: flights with no ADS-B data in any candidate archive ---------
# A completed flight that yields nothing won't change, so remember it and don't
# re-download multi-GB archives for it on every run. Delete empty_flights.json to
# force a re-attempt.

def _empty_path(cache_dir: str | Path) -> Path:
    return Path(cache_dir) / "empty_flights.json"


def load_empty(cache_dir: str | Path) -> set[str]:
    path = _empty_path(cache_dir)
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            return set()
    return set()


def save_empty(cache_dir: str | Path, keys: set[str]) -> None:
    path = _empty_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(keys)), encoding="utf-8")


# --- Registration -> icao24 cache (for non-US tails resolved via an API) ----------

def _reg_path(cache_dir: str | Path) -> Path:
    return Path(cache_dir) / "reg_icao.json"


def load_reg_cache(cache_dir: str | Path) -> dict[str, str]:
    path = _reg_path(cache_dir)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def save_reg_cache(cache_dir: str | Path, mapping: dict[str, str]) -> None:
    path = _reg_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=0, sort_keys=True), encoding="utf-8")
