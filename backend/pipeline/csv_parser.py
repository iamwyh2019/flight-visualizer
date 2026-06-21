"""Parse a Flighty CSV export into structured flight rows.

Reality check (differs from the project spec text):
- The export is COMMA-separated, not tab-separated.
- It carries many extra columns (PNR, Seat, Cabin Class, Notes, several
  "... Flighty ID" columns). We address columns by header name, never by index.
- Datetime columns have no timezone offset (e.g. "2026-06-16T16:21"); they are
  airport-local and get converted to UTC later in the pipeline (see airports.py).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Flight:
    date: str               # original "M/D/YY" string from the CSV
    airline: str
    flight: str             # carrier code + number, e.g. "UAL4194"
    from_iata: str
    to_iata: str
    diverted_to: str | None
    tail_number: str
    aircraft_type: str
    takeoff_actual: datetime | None   # naive, airport-local
    landing_actual: datetime | None   # naive, airport-local
    canceled: bool


def _parse_dt(value: str | None) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    # Flighty emits ISO-8601 without an offset, e.g. "2026-06-16T16:21".
    return datetime.fromisoformat(value)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def parse_csv(path: str | Path) -> list[Flight]:
    """Read the Flighty CSV and return every row as a Flight (including canceled)."""
    flights: list[Flight] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            flights.append(
                Flight(
                    date=_clean(row.get("Date")),
                    airline=_clean(row.get("Airline")),
                    flight=f"{_clean(row.get('Airline'))}{_clean(row.get('Flight'))}",
                    from_iata=_clean(row.get("From")),
                    to_iata=_clean(row.get("To")),
                    diverted_to=_clean(row.get("Diverted To")) or None,
                    tail_number=_clean(row.get("Tail Number")),
                    aircraft_type=_clean(row.get("Aircraft Type Name")),
                    takeoff_actual=_parse_dt(row.get("Take off (Actual)")),
                    landing_actual=_parse_dt(row.get("Landing (Actual)")),
                    canceled=_clean(row.get("Canceled")).upper() == "TRUE",
                )
            )
    return flights


def latest_flight(path: str | Path) -> Flight:
    """Return the most-recent flyable flight (skips canceled / missing-time rows).

    "Most recent" is ranked by Take off (Actual). Rows without a takeoff time or a
    tail number can't be located in the archive, so they are ignored here.
    """
    candidates = [
        f
        for f in parse_csv(path)
        if not f.canceled and f.takeoff_actual is not None and f.tail_number
    ]
    if not candidates:
        raise ValueError("No flyable flights found in CSV (all canceled or missing data).")
    return max(candidates, key=lambda f: f.takeoff_actual)
