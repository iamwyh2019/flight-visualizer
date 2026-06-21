"""Slice my leg out of the full-day trace and clean the geometry.

Output is a list of segments, each a list of [lon, lat, alt] coordinates
(GeoJSON order). We keep ONLY position + altitude and discard every other ADS-B
field. Segments handle the anti-meridian split; each is then thinned with
Douglas-Peucker (shapely) on lon/lat while retaining each kept vertex's altitude.
"""

from __future__ import annotations

from datetime import datetime

from shapely.geometry import LineString

# Trace point positional indices (readsb format).
_DT, _LAT, _LON, _ALT = 0, 1, 2, 3

# Douglas-Peucker tolerance in degrees (~roughly 10 m); near-lossless visually.
_SIMPLIFY_TOLERANCE = 0.0001


def _norm_alt(alt) -> float:
    """Normalize altitude to a float. "ground" and null -> 0.0; feet otherwise."""
    if alt is None or alt == "ground":
        return 0.0
    try:
        return float(alt)
    except (TypeError, ValueError):
        return 0.0


def slice_leg(
    trace: dict,
    start_utc: datetime,
    end_utc: datetime,
    pad_seconds: int = 300,
) -> list[list[float]]:
    """Return [lon, lat, alt] points within [start, end] (padded), in time order."""
    base = float(trace["timestamp"])
    start_ts = start_utc.timestamp() - pad_seconds
    end_ts = end_utc.timestamp() + pad_seconds

    points: list[list[float]] = []
    for arr in trace.get("trace", []):
        t = base + float(arr[_DT])
        if t < start_ts or t > end_ts:
            continue
        lat, lon = arr[_LAT], arr[_LON]
        if lat is None or lon is None:
            continue
        points.append([float(lon), float(lat), _norm_alt(arr[_ALT])])
    return points


def split_antimeridian(points: list[list[float]]) -> list[list[list[float]]]:
    """Split a coordinate list wherever consecutive longitudes jump > 180 degrees."""
    if not points:
        return []
    segments: list[list[list[float]]] = [[points[0]]]
    for prev, cur in zip(points, points[1:]):
        if abs(cur[0] - prev[0]) > 180:
            segments.append([cur])
        else:
            segments[-1].append(cur)
    return segments


def thin(segment: list[list[float]]) -> list[list[float]]:
    """Douglas-Peucker simplify a single segment on lon/lat, keeping altitude."""
    if len(segment) < 3:
        return segment
    line = LineString(segment)
    simplified = line.simplify(_SIMPLIFY_TOLERANCE, preserve_topology=False)
    return [list(c) for c in simplified.coords]


def process(trace: dict, start_utc: datetime, end_utc: datetime) -> list[list[list[float]]]:
    """Full clean: slice -> anti-meridian split -> thin each segment."""
    points = slice_leg(trace, start_utc, end_utc)
    segments = split_antimeridian(points)
    return [thin(seg) for seg in segments if len(seg) >= 2]
