"""Tail number (registration) -> icao24 hex address.

For US registrations (N-numbers) the mapping is algorithmic; we use the
`icao-nnumber-converter-us` package. Non-US registrations need a lookup table,
which is out of scope for Stage 1 Step 1 (the test flight is a US N-number).
"""

from __future__ import annotations

import icao_nnumber_converter_us as conv


def reg_to_icao(tail_number: str) -> str:
    """Return the lowercase 6-hex icao24 for a registration.

    Raises NotImplementedError for non-US tails (handled later via a lookup table).
    """
    tail = (tail_number or "").strip().upper()
    if not tail.startswith("N"):
        raise NotImplementedError(
            f"Non-US registration {tail!r}: lookup table not implemented yet "
            "(Stage 1 Step 2)."
        )
    icao = conv.n_to_icao(tail)
    if not icao:
        raise ValueError(f"Could not convert registration {tail!r} to icao24.")
    return icao.lower()
