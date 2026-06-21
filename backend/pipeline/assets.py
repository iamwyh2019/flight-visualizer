"""Auto-fetch missing display assets during a backfill.

When the log references an airline or airport we don't yet have art for, download
it on demand and append to the vendored frontend data:
  - airlines.json + airline-logos/  (name + IATA from OpenFlights, logo from Kiwi)
  - runways.json                    (runway geometry from OurAirports)
  - airport-detail.json             (taxiways/aprons/terminals from OpenStreetMap)

All best-effort: any failure is logged and skipped so it never blocks the fetch.
Bulky reference CSVs are cached under data/cache/ref/ and reused.
"""

from __future__ import annotations

import csv
import io
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
FE_DATA = ROOT / "frontend" / "data"
LOGO_DIR = FE_DATA / "airline-logos"
REF_DIR = ROOT / "data" / "cache" / "ref"

EmitFn = Callable[..., None]

_OA_AIRPORTS = "https://davidmegginson.github.io/ourairports-data/airports.csv"
_OA_RUNWAYS = "https://davidmegginson.github.io/ourairports-data/runways.csv"
_OPENFLIGHTS = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airlines.dat"
_KIWI_LOGO = "https://images.kiwi.com/airlines/128/{iata}.png"
_OVERPASS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _ref(url: str, name: str, emit: EmitFn) -> str:
    REF_DIR.mkdir(parents=True, exist_ok=True)
    p = REF_DIR / name
    if not p.exists():
        emit("log", message=f"Fetching reference data {name}…")
        p.write_bytes(_get(url, timeout=180))
    return p.read_text(encoding="utf-8", errors="ignore")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def _save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# --- airlines ---------------------------------------------------------------

# Overrides for carriers missing/wrong in OpenFlights (ICAO -> name, IATA).
_KNOWN: dict[str, tuple[str, str]] = {
    "BEL": ("Brussels Airlines", "SN"),
    "EZY": ("easyJet", "U2"),
    "EVA": ("EVA Air", "BR"),
    # Major US carriers (pre-seeded for future flights).
    "DAL": ("Delta Air Lines", "DL"),
    "SWA": ("Southwest Airlines", "WN"),
    "NKS": ("Spirit Airlines", "NK"),
    "AAY": ("Allegiant Air", "G4"),
    "HAL": ("Hawaiian Airlines", "HA"),
    "FFT": ("Frontier Airlines", "F9"),
}


def ensure_airlines(codes: list[str], emit: EmitFn) -> None:
    path = FE_DATA / "airlines.json"
    data = _load(path, {})
    missing = sorted({c for c in codes if c and c not in data})
    if not missing:
        return
    emit("log", message=f"Fetching art for {len(missing)} new airline(s): {', '.join(missing)}")
    try:
        ref = _ref(_OPENFLIGHTS, "airlines.dat", emit)
    except Exception as exc:  # noqa: BLE001
        emit("log", message=f"Airline name lookup unavailable ({exc}); using codes")
        ref = ""
    icao_map: dict[str, tuple[str, str | None]] = {}
    for row in csv.reader(io.StringIO(ref)):
        if len(row) < 8:
            continue
        icao = row[4].strip().upper()
        if not icao or icao == "\\N":
            continue
        iata = row[3].strip()
        iata = iata if iata and iata != "\\N" else None
        if icao not in icao_map or row[7].strip() == "Y":  # prefer active carrier
            icao_map[icao] = (row[1].strip(), iata)

    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    for code in missing:
        name, iata = _KNOWN.get(code) or icao_map.get(code, (code, None))
        logo = None
        if iata:
            try:
                blob = _get(_KIWI_LOGO.format(iata=iata), timeout=20)
                if len(blob) > 200:
                    (LOGO_DIR / f"{code}.png").write_bytes(blob)
                    logo = f"data/airline-logos/{code}.png"
            except Exception:  # noqa: BLE001
                pass
        data[code] = {"name": name, "iata": iata, "logo": logo}
        emit("log", message=f"  airline {code}: {name}{' + logo' if logo else ''}")
    _save(path, data)


# --- airports ---------------------------------------------------------------

def ensure_airports(codes: list[str], emit: EmitFn) -> None:
    _ensure_runways(codes, emit)
    _ensure_detail(codes, emit)


def _ensure_runways(codes: list[str], emit: EmitFn) -> None:
    path = FE_DATA / "runways.json"
    data = _load(path, {})
    missing = sorted({c for c in codes if c and c not in data})
    if not missing:
        return
    try:
        airports_csv = _ref(_OA_AIRPORTS, "airports.csv", emit)
        runways_csv = _ref(_OA_RUNWAYS, "runways.csv", emit)
    except Exception as exc:  # noqa: BLE001
        emit("log", message=f"Runway data unavailable ({exc})")
        return

    ident_by_iata: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(airports_csv)):
        ia = (row.get("iata_code") or "").strip().upper()
        if ia in missing and row.get("ident"):
            ident_by_iata[ia] = row["ident"].strip()
    iata_by_ident = {v: k for k, v in ident_by_iata.items()}

    out: dict[str, list] = {ia: [] for ia in ident_by_iata}
    for row in csv.DictReader(io.StringIO(runways_csv)):
        ia = iata_by_ident.get((row.get("airport_ident") or "").strip())
        if not ia or (row.get("closed") or "0").strip() == "1":
            continue
        le = (_fnum(row.get("le_latitude_deg")), _fnum(row.get("le_longitude_deg")))
        he = (_fnum(row.get("he_latitude_deg")), _fnum(row.get("he_longitude_deg")))
        if None in le or None in he:
            continue
        out[ia].append({
            "le": [le[0], le[1]], "he": [he[0], he[1]],
            "width_ft": _fnum(row.get("width_ft")) or 150.0,
            "le_ident": (row.get("le_ident") or "").strip(),
            "he_ident": (row.get("he_ident") or "").strip(),
        })
    for ia in missing:
        data[ia] = out.get(ia, [])
        emit("log", message=f"  airport {ia}: {len(data[ia])} runway(s)")
    _save(path, data)


def _overpass(query: str) -> dict | None:
    body = urllib.parse.urlencode({"data": query}).encode()
    for attempt in range(3):
        ep = _OVERPASS[attempt % len(_OVERPASS)]
        try:
            req = urllib.request.Request(ep, data=body, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001
            time.sleep(3 * (attempt + 1))
    return None


def _ensure_detail(codes: list[str], emit: EmitFn) -> None:
    from .airports import get_airport

    path = FE_DATA / "airport-detail.json"
    data = _load(path, {})
    missing = sorted({c for c in codes if c and c not in data})
    if not missing:
        return
    emit("log", message=f"Fetching airport diagrams for {len(missing)} airport(s) (OSM)")
    for code in missing:
        try:
            ap = get_airport(code)
        except KeyError:
            data[code] = {"type": "FeatureCollection", "features": []}
            continue
        q = f"""[out:json][timeout:90];
(
  way(around:4500,{ap.lat},{ap.lon})[aeroway=taxiway];
  way(around:4500,{ap.lat},{ap.lon})[aeroway=apron];
  way(around:4500,{ap.lat},{ap.lon})[aeroway=terminal];
  way(around:4500,{ap.lat},{ap.lon})[building=terminal];
);
out geom;"""
        res = _overpass(q)
        if res is None:
            # Transient OSM failure: don't record it, so it's retried next run.
            emit("log", message=f"  airport {code}: OSM unavailable, will retry next run")
            continue
        feats = []
        for el in (res or {}).get("elements", []):
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
            coords = [[round(p["lon"], 5), round(p["lat"], 5)] for p in g]
            if len(coords) < 2:
                continue
            if kind in ("apron", "terminal"):
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                if len(coords) < 4:
                    continue
                geom = {"type": "Polygon", "coordinates": [coords]}
            else:
                geom = {"type": "LineString", "coordinates": coords}
            feats.append({"type": "Feature", "properties": {"kind": kind}, "geometry": geom})
        data[code] = {"type": "FeatureCollection", "features": feats}
        emit("log", message=f"  airport {code}: {len(feats)} diagram feature(s)")
        _save(path, data)  # incremental
        time.sleep(2)
