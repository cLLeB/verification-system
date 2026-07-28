"""Sliding-window rate counting - accurate limits without per-event storage.

Fixed-window counters allow bursts at window edges (2x the limit across a boundary); storing
every timestamp is accurate but unbounded. The sliding-window-counter technique - used by
Cloudflare - keeps just the current and previous fixed-window counts and interpolates by how
far into the current window we are, giving a smooth, near-exact rate in constant memory. This
subsystem implements it, complementing the burst-shaped token bucket ([[ratelimit]]) with a
true rolling-rate limit.

  * ``record``   count an event for a key at a time.
  * ``rate``     the interpolated events-in-the-last-``window`` for a key.
  * ``allow``    would recording an event keep the key at/under ``limit`` per window?
                 records it and returns the decision (atomic check-and-increment).
  * ``reset``    clear a key's counters.

The estimate is ``current_count + previous_count * (1 - elapsed_fraction)`` where
``elapsed_fraction`` is how far into the current fixed window ``now`` sits - the standard
weighted approximation, accurate to a few percent for steady traffic.

Registry: ``slidingwindow.json`` (env ``FACE_SLIDINGWINDOW_FILE``).
"""

from __future__ import annotations

from typing import Optional

from ._registry import Registry

_reg = Registry("FACE_SLIDINGWINDOW_FILE", "slidingwindow.json")


def _key(tenant: Optional[str], name: str) -> str:
    return _reg.scoped(tenant, (name or '').strip())


def _roll(rec: dict, window: int, now: int) -> None:
    idx = now // window
    cur = rec.get("window_idx")
    if cur == idx:
        return
    if cur == idx - 1:
        rec["prev"] = rec.get("cur", 0)          # advance one window
    else:
        rec["prev"] = 0                          # gap: previous window is stale
    rec["cur"] = 0
    rec["window_idx"] = idx


def _estimate(rec: dict, window: int, now: int) -> float:
    idx = now // window
    if rec.get("window_idx") == idx:
        elapsed = (now % window) / window
        return rec.get("cur", 0) + rec.get("prev", 0) * (1 - elapsed)
    if rec.get("window_idx") == idx - 1:
        elapsed = (now % window) / window
        return rec.get("cur", 0) * (1 - elapsed)
    return 0.0


def record(tenant: Optional[str], name: str, window: int = 60,
           now: Optional[int] = None, amount: int = 1) -> dict:
    import time
    now = int(now if now is not None else time.time())
    window = int(window)
    if window < 1:
        raise ValueError("window must be >= 1.")
    with _reg.mutate() as data:
        rec = data.setdefault(_key(tenant, name),
                              {"cur": 0, "prev": 0, "window_idx": now // window})
        _roll(rec, window, now)
        rec["cur"] += int(amount)
        return {"count": rec["cur"], "rate": round(_estimate(rec, window, now), 3)}


def rate(tenant: Optional[str], name: str, window: int = 60,
         now: Optional[int] = None) -> float:
    import time
    now = int(now if now is not None else time.time())
    rec = _reg.load().get(_key(tenant, name))
    if not rec:
        return 0.0
    return round(_estimate(rec, int(window), now), 3)


def allow(tenant: Optional[str], name: str, limit: int, window: int = 60,
          now: Optional[int] = None) -> dict:
    import time
    now = int(now if now is not None else time.time())
    window = int(window)
    if window < 1:
        raise ValueError("window must be >= 1.")
    with _reg.mutate() as data:
        rec = data.setdefault(_key(tenant, name),
                              {"cur": 0, "prev": 0, "window_idx": now // window})
        _roll(rec, window, now)
        projected = _estimate(rec, window, now) + 1
        if projected > int(limit):
            return {"allowed": False, "rate": round(_estimate(rec, window, now), 3),
                    "limit": int(limit)}
        rec["cur"] += 1
        return {"allowed": True, "rate": round(_estimate(rec, window, now), 3)}


def reset(tenant: Optional[str], name: str) -> bool:
    with _reg.mutate() as data:
        return data.pop(_key(tenant, name), None) is not None
