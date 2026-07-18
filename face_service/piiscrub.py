"""PII scrubbing for free text — redact personal data from logs and bundles.

Structured redaction ([[anonymize]]) handles known fields, but personal data also leaks
into *free text*: an error message with an email, a support note with a phone number, a
comment containing an ID number. Before logs or support bundles leave the trust boundary
they should be scrubbed. This subsystem detects common PII patterns in text and replaces
them with typed placeholders, returning both the cleaned text and what was found.

  * ``scrub``    replace detected PII with ``[EMAIL]``/``[PHONE]``/… placeholders;
                 returns the redacted text and per-type counts.
  * ``detect``   list the matches (type, value, span) without altering the text.
  * ``has_pii``  quick boolean check.

Detects email addresses, international/local phone numbers, credit-card-like 13–16 digit
runs (validated with the Luhn check to cut false positives), IPv4 addresses, and generic
government-id-like tokens. Patterns are conservative — this reduces exposure, it is not a
guarantee of exhaustive detection.
"""

from __future__ import annotations

import re
from typing import List, Optional

from . import checkdigit

_PATTERNS = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("IPV4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?){2,4}\d{2,4}(?!\d)")),
    ("ID", re.compile(r"\b[A-Z]{2}\d{6,10}\b")),
]


def _is_card(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 13 <= len(digits) <= 16 and checkdigit.luhn_validate(digits)


def detect(text: str) -> List[dict]:
    text = text or ""
    found: List[dict] = []
    taken: List[tuple] = []                    # occupied spans (avoid overlaps)

    def overlaps(s, e):
        return any(not (e <= ts or s >= te) for ts, te in taken)

    for label, pattern in _PATTERNS:           # order = priority
        for m in pattern.finditer(text):
            s, e = m.span()
            if overlaps(s, e):
                continue
            value = m.group()
            if label == "CARD" and not _is_card(value):
                continue
            if label == "PHONE" and len(re.sub(r"\D", "", value)) < 7:
                continue
            found.append({"type": label, "value": value, "start": s, "end": e})
            taken.append((s, e))
    return sorted(found, key=lambda f: f["start"])


def scrub(text: str, mask: Optional[dict] = None) -> dict:
    matches = detect(text)
    counts: dict = {}
    out = text or ""
    for m in sorted(matches, key=lambda x: -x["start"]):   # replace right-to-left
        placeholder = (mask or {}).get(m["type"], f"[{m['type']}]")
        out = out[:m["start"]] + placeholder + out[m["end"]:]
        counts[m["type"]] = counts.get(m["type"], 0) + 1
    return {"text": out, "redactions": counts, "total": len(matches)}


def has_pii(text: str) -> bool:
    return bool(detect(text))
