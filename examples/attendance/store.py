"""Attendance's OWN domain data: a punch log (who clocked in/out, when).

The backbone never sees this - it only answers "who is this?". The vertical owns the
business record. This separation is the whole point: swap this file for exam
seatings / a payout ledger / patient visits and you have a different product on the
same identity layer.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import List, Optional

_DB = os.environ.get("ATTENDANCE_DB", os.path.join(os.path.dirname(__file__), "attendance.db"))
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS punches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        ts INTEGER NOT NULL,
        direction TEXT NOT NULL)""")
    return conn


def _day_start(ts: float) -> int:
    return int(ts - (ts % 86400))


def last_direction_today(user_id: str, now: Optional[float] = None) -> Optional[str]:
    now = now if now is not None else time.time()
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT direction FROM punches WHERE user_id=? AND ts>=? ORDER BY ts DESC LIMIT 1",
            (user_id, _day_start(now))).fetchone()
    return row[0] if row else None


def record_punch(user_id: str, now: Optional[float] = None) -> dict:
    """Toggle in/out from the person's last punch today, and store it. Returns the
    punch. First punch of the day is 'in'."""
    now = now if now is not None else time.time()
    direction = "out" if last_direction_today(user_id, now) == "in" else "in"
    with _lock, _conn() as conn:
        conn.execute("INSERT INTO punches (user_id, ts, direction) VALUES (?,?,?)",
                     (user_id, int(now), direction))
        conn.commit()
    return {"user_id": user_id, "ts": int(now), "direction": direction}


def today(now: Optional[float] = None) -> List[dict]:
    now = now if now is not None else time.time()
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT user_id, ts, direction FROM punches WHERE ts>=? ORDER BY ts DESC",
            (_day_start(now),)).fetchall()
    return [{"user_id": u, "ts": t, "direction": d} for u, t, d in rows]
