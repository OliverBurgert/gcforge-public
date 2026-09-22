"""
Deterministic LC code generation from Adventure Lab UUID.

The canonical algorithm maps an adventure's globally-stable UUID to an LC
code using base31 encoding, making codes identical across different lab2gpx
installations and any other tool that implements this algorithm.

Current lab2gpx behaviour
--------------------------
Each installation maintains a per-instance SQLite counter (starting at
offset 30000) and encodes the row-id as base31.  The same adventure therefore
receives different LC codes on different machines, making codes non-portable.

This replacement
-----------------
A pure function that derives the LC code deterministically from the adventure
UUID present in every lab2gpx GPX export.  No database, no state, no side
effects.

Algorithm
---------
1. Parse the UUID (with or without hyphens) as a 128-bit integer.
2. Reduce modulo 31^LENGTH to obtain a value in [0, 31^LENGTH).
3. Encode as LENGTH zero-padded digits in base31 using the standard GC
   alphabet: ``0123456789ABCDEFGHJKMNPQRTVWXYZ``
   (identical to geocaching.com and existing lab2gpx codes; excludes I/L/O/S/U
   to avoid visual ambiguity with digits).
4. Prefix ``LC``.

Collision analysis (birthday paradox)
--------------------------------------
LENGTH=8  →  31^8  ≈ 852 billion combinations.
At 10 000 adventures:  P(collision) ≈ 0.006 %
At 100 000 adventures: P(collision) ≈ 0.6 %

For a lab2gpx pull request
---------------------------
Extract only the three module-level constants (_ALPHABET, _BASE,
_DEFAULT_LENGTH) and the ``uuid_to_lc_code()`` function.  The function has
no external dependencies and is compatible with Python 3.6+.
"""
from __future__ import annotations

# Base31 alphabet — identical to the geocaching.com GC code alphabet.
# Excludes I, L, O, S, U to avoid visual ambiguity with digits.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRTVWXYZ"
_BASE = len(_ALPHABET)  # 31

# Number of base31 characters after the "LC" prefix.
# 31^8 ≈ 852 B combinations; collision probability < 0.006 % for 10 K adventures.
_DEFAULT_LENGTH = 8


def uuid_to_lc_code(uuid: str, length: int = _DEFAULT_LENGTH) -> str:
    """Return the deterministic LC code for an Adventure Lab UUID.

    The same UUID always produces the same code regardless of which tool
    or lab2gpx installation exported the adventure.

    Parameters
    ----------
    uuid:
        Adventure UUID, with or without hyphens.
        Example: ``"550e8400-e29b-41d4-a716-446655440000"``
    length:
        Number of base31 characters after the ``LC`` prefix.
        Default is 8 (31^8 ≈ 852 B combinations).

    Returns
    -------
    str
        LC code, e.g. ``"LC1A2B3C4D"``.

    Raises
    ------
    ValueError
        If *uuid* is not a valid hex string after stripping hyphens.
    """
    uid_int = int(uuid.replace("-", ""), 16)
    n = uid_int % (_BASE ** length)
    chars: list[str] = []
    for _ in range(length):
        chars.append(_ALPHABET[n % _BASE])
        n //= _BASE
    return "LC" + "".join(reversed(chars))
