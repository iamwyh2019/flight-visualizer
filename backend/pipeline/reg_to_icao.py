"""Tail number (registration) -> icao24 hex address.

- US registrations (N-numbers): algorithmic, via `icao-nnumber-converter-us`.
- B- registrations (mainland China / Taiwan / Hong Kong): skipped by configuration.
- Everything else (e.g. UK G-, etc.): resolved through the free adsbdb API and
  cached locally. Flighty strips the dash from registrations (e.g. "GEZUS"), so we
  try plausible dashed forms ("G-EZUS") until one resolves.
"""

from __future__ import annotations

import httpx
import icao_nnumber_converter_us as conv

from . import cache as _cache

_ADSBDB = "https://api.adsbdb.com/v0/aircraft/"


def _dash_variants(tail: str):
    """Yield candidate registrations: as-is, then dash after the 1st / 2nd char."""
    seen = []
    for cand in (tail, f"{tail[:1]}-{tail[1:]}", f"{tail[:2]}-{tail[2:]}"):
        if len(tail) > 1 and cand not in seen:
            seen.append(cand)
            yield cand


def _lookup_adsbdb(tail: str) -> str | None:
    for cand in _dash_variants(tail):
        try:
            resp = httpx.get(_ADSBDB + cand, timeout=15.0)
            if resp.status_code != 200:
                continue
            aircraft = resp.json().get("response", {}).get("aircraft", {})
            mode_s = (aircraft or {}).get("mode_s")
            if mode_s:
                return mode_s.lower()
        except (httpx.HTTPError, ValueError, AttributeError):
            continue
    return None


def reg_to_icao(tail_number: str, cache_dir: str | None = None) -> str:
    """Return the lowercase 6-hex icao24 for a registration.

    Raises NotImplementedError for registrations we intentionally skip (B-) or
    cannot resolve, so the caller can log and move on.
    """
    tail = (tail_number or "").strip().upper()
    if not tail:
        raise ValueError("empty registration")

    if tail.startswith("N"):
        icao = conv.n_to_icao(tail)
        if not icao:
            raise ValueError(f"Could not convert registration {tail!r} to icao24.")
        return icao.lower()

    if tail.startswith("B"):
        raise NotImplementedError("B- registration (China/Taiwan/HK) skipped by configuration")

    # Non-US, non-B: resolve via API, cached.
    reg_cache = _cache.load_reg_cache(cache_dir) if cache_dir else {}
    if tail in reg_cache:
        return reg_cache[tail]

    icao = _lookup_adsbdb(tail)
    if icao is None:
        raise NotImplementedError(f"could not resolve registration {tail} to icao24")

    if cache_dir:
        reg_cache[tail] = icao
        _cache.save_reg_cache(cache_dir, reg_cache)
    return icao
