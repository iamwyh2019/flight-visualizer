"""One-time builder: fetch taxiway/apron/terminal geometry from OpenStreetMap
(Overpass) for the airports in the Flighty log, vendored as airport-detail.json.

Output shape: { IATA: GeoJSON FeatureCollection } where each feature's
properties.kind is one of taxiway | apron | terminal. Runways come from the
separate runways.json (OurAirports), so they're not fetched here.
"""

from __future__ import annotations

import glob
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.pipeline.csv_parser import parse_csv  # noqa: E402
from backend.pipeline.airports import get_airport  # noqa: E402

# Mirrors tried in order; kumi/fr are usually less loaded than the main instance.
ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]


def overpass(query: str, attempts: int = 4) -> dict:
    """Query Overpass with endpoint rotation + exponential backoff."""
    data = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(attempts):
        ep = ENDPOINTS[attempt % len(ENDPOINTS)]
        try:
            req = urllib.request.Request(ep, data=data)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(4 * (attempt + 1))  # 4s, 8s, 12s, ...
    raise last


def rnd(v: float) -> float:
    return round(v, 5)


def main() -> None:
    codes = set()
    for f in parse_csv(sorted(glob.glob("flighty-logs/*.csv"))[-1]):
        for c in (f.from_iata, f.to_iata, f.diverted_to):
            if c:
                codes.add(c.strip().upper())
    codes = sorted(codes)
    dest = Path("frontend/data/airport-detail.json")

    # Resume: keep airports already fetched in a previous run.
    out: dict[str, dict] = {}
    if dest.exists():
        try:
            out = json.loads(dest.read_text())
        except ValueError:
            out = {}

    for i, code in enumerate(codes, 1):
        if code in out:
            print(f"  [{i}/{len(codes)}] {code}: already have it, skip", flush=True)
            continue
        try:
            ap = get_airport(code)
        except Exception:  # noqa: BLE001
            print(f"  [{i}/{len(codes)}] {code}: unknown airport, skip", flush=True)
            continue
        query = f"""[out:json][timeout:90];
(
  way(around:4500,{ap.lat},{ap.lon})[aeroway=taxiway];
  way(around:4500,{ap.lat},{ap.lon})[aeroway=apron];
  way(around:4500,{ap.lat},{ap.lon})[aeroway=terminal];
  way(around:4500,{ap.lat},{ap.lon})[building=terminal];
);
out geom;"""
        try:
            res = overpass(query)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(codes)}] {code}: FAILED {exc}", flush=True)
            continue

        feats = []
        for el in res.get("elements", []):
            g = el.get("geometry")
            if not g:
                continue
            tags = el.get("tags", {})
            if tags.get("aeroway") == "taxiway":
                kind = "taxiway"
            elif tags.get("aeroway") == "apron":
                kind = "apron"
            elif tags.get("aeroway") == "terminal" or tags.get("building") == "terminal":
                kind = "terminal"
            else:
                continue
            coords = [[rnd(p["lon"]), rnd(p["lat"])] for p in g]
            if len(coords) < 2:
                continue
            if kind in ("apron", "terminal"):
                if coords[0] != coords[-1]:
                    coords = coords + [coords[0]]
                if len(coords) < 4:
                    continue
                geom = {"type": "Polygon", "coordinates": [coords]}
            else:
                geom = {"type": "LineString", "coordinates": coords}
            feats.append({"type": "Feature", "properties": {"kind": kind}, "geometry": geom})

        out[code] = {"type": "FeatureCollection", "features": feats}
        counts: dict[str, int] = {}
        for ft in feats:
            counts[ft["properties"]["kind"]] = counts.get(ft["properties"]["kind"], 0) + 1
        print(f"  [{i}/{len(codes)}] {code}: {len(feats)} features {counts}", flush=True)
        # Save incrementally so progress survives crashes / can be resumed.
        dest.write_text(json.dumps(out))
        time.sleep(5)

    print(f"\nWrote {dest} ({dest.stat().st_size} bytes) for {len(out)} airports", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
