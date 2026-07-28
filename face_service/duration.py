"""Duration parsing and formatting - human-friendly time spans.

TTLs, lease lengths, quiet-hours windows and retention periods are entered and displayed as
human durations ("30m", "2h30m", "7d"), not raw seconds. This subsystem converts between the
two: parse a compact duration string to seconds, and format seconds back to a compact or
verbose string. It's a small, dependency-free utility the config-facing subsystems
([[reminders]], [[keyrotation]], [[quiethours]], [[retention]]) can share instead of
re-implementing.

  * ``parse``      "1h30m" / "2d" / "45s" → seconds (supports w/d/h/m/s, combined).
  * ``format``     seconds → compact "1h 30m"; ``verbose=True`` → "1 hour 30 minutes".
  * ``humanize``   a rough, single-unit approximation ("about 2 hours").

Units: ``w`` weeks, ``d`` days, ``h`` hours, ``m`` minutes, ``s`` seconds. Parsing is
case-insensitive and tolerant of spaces ("1h 30m"); a bare integer is treated as seconds.
"""

from __future__ import annotations

import re

_UNIT_SECONDS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
_TOKEN = re.compile(r"(\d+)\s*([wdhms])", re.IGNORECASE)


def parse(text: str) -> int:
    s = (text or "").strip().lower()
    if not s:
        raise ValueError("duration is required.")
    if s.isdigit():
        return int(s)
    total, consumed = 0, 0
    for m in _TOKEN.finditer(s):
        total += int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
        consumed += len(m.group(0))
    if consumed == 0 or len(re.sub(r"\s", "", s)) != len(re.sub(r"\s", "", "".join(
            m.group(0) for m in _TOKEN.finditer(s)))):
        raise ValueError(f"unparseable duration: {text!r}")
    return total


_ORDER = [("w", "week"), ("d", "day"), ("h", "hour"), ("m", "minute"), ("s", "second")]


def format(seconds: int, verbose: bool = False, max_units: int = 0) -> str:
    seconds = int(seconds)
    if seconds == 0:
        return "0 seconds" if verbose else "0s"
    sign = "-" if seconds < 0 else ""
    rem = abs(seconds)
    parts = []
    for short, long in _ORDER:
        unit = _UNIT_SECONDS[short]
        if rem >= unit:
            n, rem = divmod(rem, unit)
            if verbose:
                parts.append(f"{n} {long}{'s' if n != 1 else ''}")
            else:
                parts.append(f"{n}{short}")
    if max_units and max_units > 0:
        parts = parts[:max_units]
    return sign + (" ".join(parts))


def humanize(seconds: int) -> str:
    seconds = int(seconds)
    rem = abs(seconds)
    for short, long in _ORDER:
        unit = _UNIT_SECONDS[short]
        if rem >= unit:
            n = round(rem / unit)
            return f"about {n} {long}{'s' if n != 1 else ''}"
    return "0 seconds"
