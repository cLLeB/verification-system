"""Welfare program's OWN ledger: beneficiaries + payouts. The backbone answers
"who is this?" and "is this face already registered?"; this file owns the money
record. Ghost/duplicate elimination happens by asking the backbone to IDENTIFY a
new registrant against everyone already enrolled (done in app.py) — here we just
keep the authoritative list and the payout log.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import List, Optional

_DB = os.environ.get("WELFARE_DB", os.path.join(os.path.dirname(__file__), "welfare.db"))
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS beneficiaries (
        name TEXT PRIMARY KEY, program TEXT, registered INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS payouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, amount REAL, ts INTEGER)""")
    return conn


def is_registered(name: str) -> bool:
    with _lock, _conn() as conn:
        return conn.execute("SELECT 1 FROM beneficiaries WHERE name=?", (name,)).fetchone() is not None


def register(name: str, program: str, now: Optional[float] = None) -> bool:
    """Add a beneficiary. Returns False if this NAME already exists (the FACE-level
    duplicate check is the backbone's job, in app.py, before calling this)."""
    now = now if now is not None else time.time()
    with _lock, _conn() as conn:
        if conn.execute("SELECT 1 FROM beneficiaries WHERE name=?", (name,)).fetchone():
            return False
        conn.execute("INSERT INTO beneficiaries (name, program, registered) VALUES (?,?,?)",
                     (name, program, int(now)))
        conn.commit()
    return True


def record_payout(name: str, amount: float, now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    with _lock, _conn() as conn:
        conn.execute("INSERT INTO payouts (name, amount, ts) VALUES (?,?,?)",
                     (name, float(amount), int(now)))
        conn.commit()
    return {"name": name, "amount": float(amount), "ts": int(now)}


def summary() -> dict:
    with _lock, _conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM beneficiaries").fetchone()[0]
        total = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payouts").fetchone()[0]
        paid = conn.execute("SELECT COUNT(*) FROM payouts").fetchone()[0]
    return {"beneficiaries": n, "payouts": paid, "total_paid": total}


def beneficiaries() -> List[dict]:
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT name, program, registered FROM beneficiaries ORDER BY registered").fetchall()
    return [{"name": n, "program": p, "registered": r} for n, p, r in rows]
