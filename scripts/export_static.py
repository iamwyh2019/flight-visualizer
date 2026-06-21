"""Export the cached flights to a static file for a no-backend (static) deploy.

Writes frontend/data/flights.json (same shape as GET /api/flights). After running
this, the entire frontend/ directory is a self-contained static site — the
Visualize view falls back to this file when /api/flights isn't available.

Usage:  python scripts/export_static.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.pipeline.collect import build_flights_payload  # noqa: E402

CACHE_DIR = ROOT / "data" / "cache"
DEST = ROOT / "frontend" / "data" / "flights.json"


def main() -> None:
    payload = build_flights_payload(CACHE_DIR)
    DEST.write_text(json.dumps(payload))
    print(
        f"Wrote {DEST.relative_to(ROOT)}: {len(payload['flights'])} flights, "
        f"{len(payload['airports'])} airports ({DEST.stat().st_size / 1e6:.1f} MB)"
    )


if __name__ == "__main__":
    main()
