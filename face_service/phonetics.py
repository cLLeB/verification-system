"""Phonetic name matching — catch names that sound alike but spell differently.

Transliterated and mis-heard names ("Mohammed"/"Muhammad", "Catherine"/"Kathryn") defeat
exact matching and even trip up character-level fuzzy matching. Phonetic algorithms encode
a name by how it *sounds*, so variants collapse to the same code. This subsystem provides
Soundex and NYSIIS encoders and a match helper — a useful pre-filter or signal for
[[sanctions]] screening and duplicate-enrolment detection. Pure and stateless.

  * ``soundex``   the classic 4-char Soundex code (Robert/Rupert → ``R163``).
  * ``nysiis``    the New York State Identification and Intelligence System code, which
                  handles more cases than Soundex.
  * ``sounds_like`` do two names share a phonetic code (Soundex by default)?

These are coarse by design — they group plausibly-similar names and will occasionally
group unrelated ones; use them to *widen* a candidate set, then confirm with a stronger
comparison.
"""

from __future__ import annotations

import re

_SOUNDEX_MAP = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"),
                **dict.fromkeys("dt", "3"), **dict.fromkeys("l", "4"),
                **dict.fromkeys("mn", "5"), **dict.fromkeys("r", "6")}


def _clean(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def soundex(name: str) -> str:
    s = _clean(name)
    if not s:
        return ""
    first = s[0].upper()
    codes = [_SOUNDEX_MAP.get(c, "") for c in s]
    result = first
    prev = _SOUNDEX_MAP.get(s[0], "")
    for i in range(1, len(s)):
        code = codes[i]
        # 'h' and 'w' don't reset the "previous code" rule; vowels do
        if code and code != prev:
            result += code
        if s[i] not in ("h", "w"):
            prev = code
        if len(result) >= 4:
            break
    return (result + "000")[:4]


def nysiis(name: str) -> str:
    s = _clean(name).upper()
    if not s:
        return ""
    # prefixes
    for pre, rep in (("MAC", "MCC"), ("KN", "NN"), ("K", "C"),
                     ("PH", "FF"), ("PF", "FF"), ("SCH", "SSS")):
        if s.startswith(pre):
            s = rep + s[len(pre):]
            break
    # suffixes
    for suf, rep in (("EE", "Y"), ("IE", "Y"), ("DT", "D"), ("RT", "D"),
                     ("RD", "D"), ("NT", "D"), ("ND", "D")):
        if s.endswith(suf):
            s = s[:-len(suf)] + rep
            break
    key = s[0]
    prev = s[0]
    i = 1
    while i < len(s):
        c = s[i]
        if c in "AEIOU":
            c = "A"
            if s[i] == "E" and i + 1 < len(s) and s[i + 1] == "V":
                c = "AF"
                i += 1
        elif c == "Q":
            c = "G"
        elif c == "Z":
            c = "S"
        elif c == "M":
            c = "N"
        elif c == "K":
            c = "C"
        elif c == "S" and s[i:i + 3] == "SCH":
            c = "SSS"
            i += 2
        elif c == "P" and s[i:i + 2] == "PH":
            c = "FF"
            i += 1
        elif c == "H" and (prev not in "AEIOU" or (i + 1 < len(s) and s[i + 1] not in "AEIOU")):
            c = prev
        elif c == "W" and prev in "AEIOU":
            c = prev
        add = c
        if add and add[-1] != key[-1]:
            key += add
        prev = s[i]
        i += 1
    # trim trailing S, AY->Y, trailing A
    if key.endswith("S") and len(key) > 1:
        key = key[:-1]
    if key.endswith("AY"):
        key = key[:-2] + "Y"
    if key.endswith("A") and len(key) > 1:
        key = key[:-1]
    return key


def sounds_like(a: str, b: str, algorithm: str = "soundex") -> bool:
    if algorithm == "nysiis":
        enc = nysiis
    elif algorithm == "soundex":
        enc = soundex
    else:
        raise ValueError("algorithm must be 'soundex' or 'nysiis'.")
    ca, cb = enc(a), enc(b)
    return bool(ca) and ca == cb
