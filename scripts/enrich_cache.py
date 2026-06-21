"""Backfill new properties into already-cached per-flight GeoJSON from the CSV.

Currently adds `takeoff` (so the Visualize list can sort same-day flights in
reverse-chronological order). Idempotent — safe to re-run.

Usage:  python scripts/enrich_cache.py
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.pipeline import cache  # noqa: E402
from backend.pipeline.csv_parser import parse_csv  # noqa: E402

CACHE_DIR = ROOT / "data" / "cache"


def main() -> None:
    csv = sorted(glob.glob(str(ROOT / "flighty-logs" / "*.csv")))[-1]
    updated = 0
    for f in parse_csv(csv):
        if not f.takeoff_actual:
            continue
        path = cache.cache_path(CACHE_DIR, f)
        if not path.exists():
            continue
        feat = json.loads(path.read_text(encoding="utf-8"))
        props = feat.get("properties", {})
        want = {
            "takeoff": f.takeoff_actual.isoformat() if f.takeoff_actual else None,
            "landing": f.landing_actual.isoformat() if f.landing_actual else None,
        }
        if any(props.get(k) != v for k, v in want.items()):
            props.update(want)
            feat["properties"] = props
            path.write_text(json.dumps(feat), encoding="utf-8")
            updated += 1
    print(f"Enriched {updated} cached flight(s) with takeoff time")


if __name__ == "__main__":
    main()
