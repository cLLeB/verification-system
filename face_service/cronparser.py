"""Cron expression parser — validate schedules and compute next run times.

Scheduled work in the platform (digest sends, access reviews, report generation, key
rotation reminders) is naturally expressed as cron. Rather than depend on a scheduler
library, this subsystem parses standard 5-field cron expressions, tests whether a given
minute matches, and computes the next run time after an instant — enough to drive any of
the pull-based schedulers ([[reminders]], [[jobs]]) deterministically.

  * ``parse``     validate an expression into per-field allowed-value sets.
  * ``matches``   does a UTC epoch-second (truncated to its minute) satisfy the cron?
  * ``next_run``  the next matching epoch-second strictly after a given time.
  * ``describe``  the parsed field sets, for display/debugging.

Supports ``*``, single values, ``a-b`` ranges, ``a,b,c`` lists, and ``*/n`` or ``a-b/n``
steps, across minute (0-59), hour (0-23), day-of-month (1-31), month (1-12), and
day-of-week (0-6, Sunday=0). When both day-of-month and day-of-week are restricted, a
minute matches if *either* does — the standard Vixon-cron OR semantics.

Pure/stateless — no registry.
"""

from __future__ import annotations

import time as _time
from typing import Optional, Set

_BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
_NAMES = ["minute", "hour", "day", "month", "weekday"]


def _parse_field(spec: str, lo: int, hi: int) -> Set[int]:
    values: Set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty cron field element.")
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step < 1:
                raise ValueError("step must be >= 1.")
        else:
            base = part
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(base)
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron field out of range [{lo},{hi}]: {part}")
        values.update(range(start, end + 1, step))
    return values


def parse(expr: str) -> dict:
    fields = (expr or "").split()
    if len(fields) != 5:
        raise ValueError("cron expression must have 5 fields.")
    parsed = {}
    for name, spec, (lo, hi) in zip(_NAMES, fields, _BOUNDS):
        parsed[name] = _parse_field(spec, lo, hi)
    return parsed


def _matches_parsed(p: dict, tm) -> bool:
    if tm.tm_min not in p["minute"]:
        return False
    if tm.tm_hour not in p["hour"]:
        return False
    if tm.tm_mon not in p["month"]:
        return False
    dow = tm.tm_wday                   # Python: Mon=0..Sun=6
    cron_dow = (dow + 1) % 7           # cron: Sun=0..Sat=6
    dom_restricted = p["day"] != set(range(1, 32))
    dow_restricted = p["weekday"] != set(range(0, 7))
    dom_ok = tm.tm_mday in p["day"]
    dow_ok = cron_dow in p["weekday"]
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok        # Vixie-cron OR semantics
    return dom_ok and dow_ok


def matches(expr: str, when: int) -> bool:
    return _matches_parsed(parse(expr), _time.gmtime(int(when)))


def next_run(expr: str, after: Optional[int] = None, horizon_days: int = 366) -> Optional[int]:
    p = parse(expr)
    after = int(after if after is not None else _time.time())
    # start from the next whole minute
    t = (after // 60 + 1) * 60
    limit = t + int(horizon_days) * 86400
    while t <= limit:
        if _matches_parsed(p, _time.gmtime(t)):
            return t
        t += 60
    return None


def describe(expr: str) -> dict:
    p = parse(expr)
    return {name: sorted(p[name]) for name in _NAMES}
