"""Decode an adsb.lol trace_full file and verify it's the right aircraft.

The trace file is gzip-compressed JSON even though its name ends in `.json`.
Top-level shape (per readsb README-json):
    { "icao": "...", "r": "<reg>", "t": "<type>", "timestamp": <unix s>, "trace": [...] }
Each trace point is a positional array:
    [dt_s, lat, lon, alt, gs, track, flags, vrate, details, source, ...]
"""

from __future__ import annotations

import gzip
import json


def decode_trace(raw: bytes) -> dict:
    """Gunzip (if needed) and JSON-parse the trace file bytes."""
    if raw[:2] == b"\x1f\x8b":  # gzip magic
        raw = gzip.decompress(raw)
    return json.loads(raw)


def _normalize_reg(reg: str) -> str:
    """Upper-case and drop non-alphanumerics so 'G-EZUS' == 'GEZUS' (Flighty strips dashes)."""
    return "".join(c for c in (reg or "").upper() if c.isalnum())


def verify_registration(trace: dict, expected_reg: str, emit=None) -> bool:
    """Check the trace's `r` matches the expected registration (dash/case-insensitive)."""
    actual = (trace.get("r") or "").strip().upper()
    ok = _normalize_reg(actual) == _normalize_reg(expected_reg)
    if not ok and emit is not None:
        emit("log", message=f"WARNING: trace r={actual!r} != expected {expected_reg!r}")
    return ok
