"""One-time migration to the format-independent flight cache key.

The cache key used to embed the raw CSV date string. Flighty exported that as
"M/D/YY" for a long time, then switched to ISO "YYYY-MM-DD" — which silently
changed every key, so the whole cache missed and a full multi-GB re-backfill
kicked off. The key is now date(ISO) + route + flight + tail (see
`cache.flight_key`), which is stable across that format change.

This renames every existing per-flight GeoJSON to the new key (derived from the
file's own stored properties) and translates the negative-cache (empty_flights)
keys, so nothing already fetched is downloaded again. Idempotent — safe to re-run.

    python3 scripts/migrate_cache_keys.py
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

CACHE = ROOT / "data" / "cache"
FLIGHTS = CACHE / "flights"


def migrate_features() -> None:
    renamed = redated = same = 0
    for path in sorted(FLIGHTS.glob("*.geojson")):
        feature = json.loads(path.read_text(encoding="utf-8"))
        p = feature["properties"]
        # Normalize the stored display date to ISO in place (the frontend shows it
        # verbatim, so mixed M/D/YY vs ISO would render inconsistently).
        iso = cache.canon_date(p["date"])
        if p["date"] != iso:
            p["date"] = iso
            path.write_text(json.dumps(feature), encoding="utf-8")
            redated += 1
        new_key = cache._key(p["date"], p["from"], p["to"], p["flight"], p["tail_number"])
        target = FLIGHTS / f"{new_key}.geojson"
        if target == path:
            same += 1
            continue
        path.rename(target)  # POSIX rename overwrites; identical flight if it collides
        renamed += 1
    print(f"features: {renamed} renamed, {redated} re-dated, {same} already canonical")


def migrate_empty() -> None:
    path = CACHE / "empty_flights.json"
    if not path.exists():
        print("empty_flights.json: none")
        return
    old_keys = set(json.loads(path.read_text(encoding="utf-8")))

    # Build old-style-key -> new-key from the current CSV by reconstructing the
    # former "M/D/YY" date the old key would have used for each flight.
    csv = sorted(glob.glob(str(ROOT / "flighty-logs" / "*.csv")))[-1]
    old_to_new: dict[str, str] = {}
    for f in parse_csv(csv):
        iso = cache.canon_date(f.date)
        try:
            y, m, d = (int(x) for x in iso.split("-"))
        except ValueError:
            continue  # unrecognized date; can't reconstruct the old key
        old_raw = f"{m}/{d}/{y % 100:02d}"
        old_key = cache._sanitize(f"{old_raw}_{f.flight}_{f.tail_number}")
        old_to_new[old_key] = cache.flight_key(f)

    new_keys = sorted({old_to_new.get(k, k) for k in old_keys})
    matched = sum(1 for k in old_keys if k in old_to_new)
    path.write_text(json.dumps(new_keys), encoding="utf-8")
    print(f"empty_flights.json: {matched}/{len(old_keys)} keys remapped")


if __name__ == "__main__":
    migrate_features()
    migrate_empty()
