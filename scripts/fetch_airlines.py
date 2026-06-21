"""One-time builder: vendor airline names + logos for the airlines in the log.

Produces frontend/data/airlines.json ({ICAO: {name, iata, logo}}) and downloads
each logo into frontend/data/airline-logos/{icao}.png (cached locally, offline).
Logos come from Kiwi's public airline-logo CDN, keyed by IATA code.
"""

from __future__ import annotations

import glob
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.pipeline.csv_parser import parse_csv  # noqa: E402

# ICAO airline code -> (full name, IATA code). Stable, well-known carriers.
AIRLINES = {
    "AAL": ("American Airlines", "AA"),
    "ASA": ("Alaska Airlines", "AS"),
    "BAW": ("British Airways", "BA"),
    "CPA": ("Cathay Pacific", "CX"),
    "CSN": ("China Southern", "CZ"),
    "EVA": ("EVA Air", "BR"),
    "EZY": ("easyJet", "U2"),
    "UAL": ("United Airlines", "UA"),
    "DAL": ("Delta Air Lines", "DL"),
    "SWA": ("Southwest Airlines", "WN"),
    "JBU": ("JetBlue", "B6"),
    "ACA": ("Air Canada", "AC"),
    "DLH": ("Lufthansa", "LH"),
    "AFR": ("Air France", "AF"),
}
LOGO_URL = "https://images.kiwi.com/airlines/128/{iata}.png"


def main() -> None:
    codes = sorted({f.airline for f in parse_csv(sorted(glob.glob("flighty-logs/*.csv"))[-1]) if f.airline})
    logo_dir = Path("frontend/data/airline-logos")
    logo_dir.mkdir(parents=True, exist_ok=True)

    out: dict[str, dict] = {}
    for icao in codes:
        if icao not in AIRLINES:
            print(f"  {icao}: unknown airline code, name only")
            out[icao] = {"name": icao, "iata": None, "logo": None}
            continue
        name, iata = AIRLINES[icao]
        logo_rel = None
        try:
            req = urllib.request.Request(LOGO_URL.format(iata=iata), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                blob = r.read()
            if len(blob) > 200:  # sanity: a real PNG, not an error stub
                (logo_dir / f"{icao}.png").write_bytes(blob)
                logo_rel = f"data/airline-logos/{icao}.png"
                print(f"  {icao} ({iata}) {name}: logo {len(blob)} bytes")
            else:
                print(f"  {icao} ({iata}) {name}: logo too small, skipped")
        except Exception as exc:  # noqa: BLE001
            print(f"  {icao} ({iata}) {name}: logo failed ({exc})")
        out[icao] = {"name": name, "iata": iata, "logo": logo_rel}

    Path("frontend/data/airlines.json").write_text(json.dumps(out, indent=0))
    print(f"\nWrote frontend/data/airlines.json for {len(out)} airlines")


if __name__ == "__main__":
    main()
