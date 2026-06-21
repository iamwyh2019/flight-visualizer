"""IATA airport lookup + airport-local -> UTC conversion.

Flighty's "Take off (Actual)" is in the departure airport's local time and
"Landing (Actual)" is in the arrival airport's local time. We convert each using
the airport's IANA timezone (from the `airportsdata` package) so we can slice the
aircraft's UTC trace correctly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import airportsdata

# Loaded once at import; keyed by IATA code.
_IATA = airportsdata.load("IATA")


@dataclass
class Airport:
    iata: str
    name: str
    lat: float
    lon: float
    tz: str


def get_airport(iata: str) -> Airport:
    code = (iata or "").strip().upper()
    rec = _IATA.get(code)
    if rec is None:
        raise KeyError(f"Unknown IATA airport code: {code!r}")
    return Airport(
        iata=code,
        name=rec["name"],
        lat=rec["lat"],
        lon=rec["lon"],
        tz=rec["tz"],
    )


def local_to_utc(naive_local: datetime, iata: str) -> datetime:
    """Interpret a naive datetime as local to `iata` and return an aware UTC datetime."""
    airport = get_airport(iata)
    aware_local = naive_local.replace(tzinfo=ZoneInfo(airport.tz))
    return aware_local.astimezone(timezone.utc)
