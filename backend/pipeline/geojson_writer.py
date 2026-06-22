"""Build a GeoJSON Feature for one flight from cleaned segments + CSV metadata.

geometry holds coordinates only ([lon, lat, alt]); properties carry only the
CSV-derived display/filter fields. A single segment -> LineString; multiple
(anti-meridian-split) segments -> MultiLineString.
"""

from __future__ import annotations

from datetime import datetime

from .csv_parser import Flight


def build_feature(
    flight: Flight,
    segments: list[list[list[float]]],
    takeoff_utc: datetime | None = None,
    landing_utc: datetime | None = None,
) -> dict:
    if len(segments) == 1:
        geometry = {"type": "LineString", "coordinates": segments[0]}
    else:
        geometry = {"type": "MultiLineString", "coordinates": segments}

    # takeoff/landing are stored as UTC (offset-aware) so a plain landing-takeoff
    # subtraction is a correct duration regardless of the airports' timezones.
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "date": flight.date,
            "takeoff": takeoff_utc.isoformat() if takeoff_utc else None,
            "landing": landing_utc.isoformat() if landing_utc else None,
            "airline": flight.airline,
            "flight": flight.flight,
            "aircraft_type": flight.aircraft_type,
            "tail_number": flight.tail_number,
            "from": flight.from_iata,
            "to": flight.to_iata,
            "diverted_to": flight.diverted_to,
        },
    }
