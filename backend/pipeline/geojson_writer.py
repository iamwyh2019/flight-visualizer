"""Build a GeoJSON Feature for one flight from cleaned segments + CSV metadata.

geometry holds coordinates only ([lon, lat, alt]); properties carry only the
CSV-derived display/filter fields. A single segment -> LineString; multiple
(anti-meridian-split) segments -> MultiLineString.
"""

from __future__ import annotations

from .csv_parser import Flight


def build_feature(flight: Flight, segments: list[list[list[float]]]) -> dict:
    if len(segments) == 1:
        geometry = {"type": "LineString", "coordinates": segments[0]}
    else:
        geometry = {"type": "MultiLineString", "coordinates": segments}

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "date": flight.date,
            "airline": flight.airline,
            "flight": flight.flight,
            "aircraft_type": flight.aircraft_type,
            "tail_number": flight.tail_number,
            "from": flight.from_iata,
            "to": flight.to_iata,
            "diverted_to": flight.diverted_to,
        },
    }
