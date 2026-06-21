"""Aggregate the per-flight cache into one payload (shared by the live endpoint
and the static export)."""

from __future__ import annotations

import json
from pathlib import Path

from .airports import get_airport


def build_flights_payload(cache_dir: str | Path) -> dict:
    """Read every cached per-flight GeoJSON and return the Visualize payload:
    { flights:[feature...], airports:{IATA:{lat,lon,name}}, routes:{key:count}, route_max }.
    """
    features: list[dict] = []
    for path in sorted((Path(cache_dir) / "flights").glob("*.geojson")):
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

    return {
        "flights": features,
        "airports": airports,
        "routes": route_counts,
        "route_max": max(route_counts.values()) if route_counts else 1,
    }
