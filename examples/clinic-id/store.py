"""The clinic's OWN records: patients + visit history. The backbone answers "who is
this patient?" (cardless, by face or palm); this file owns the medical record and
the visit log. Adaptive enrolment on the backbone keeps recognising a patient across
visits over months/years.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import List, Optional

_DB = os.environ.get("CLINIC_DB", os.path.join(os.path.dirname(__file__), "clinic.db"))
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS patients (
        mrn TEXT PRIMARY KEY, name TEXT, registered INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS visits (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mrn TEXT, ts INTEGER, note TEXT)""")
    return conn


def register_patient(mrn: str, name: str, now: Optional[float] = None) -> bool:
    now = now if now is not None else time.time()
    with _lock, _conn() as conn:
        if conn.execute("SELECT 1 FROM patients WHERE mrn=?", (mrn,)).fetchone():
            return False
        conn.execute("INSERT INTO patients (mrn, name, registered) VALUES (?,?,?)",
                     (mrn, name, int(now)))
        conn.commit()
    return True


def patient(mrn: str) -> Optional[dict]:
    with _lock, _conn() as conn:
        row = conn.execute("SELECT mrn, name, registered FROM patients WHERE mrn=?", (mrn,)).fetchone()
    return {"mrn": row[0], "name": row[1], "registered": row[2]} if row else None


def add_visit(mrn: str, note: str = "", now: Optional[float] = None) -> dict:
    now = now if now is not None else time.time()
    with _lock, _conn() as conn:
        conn.execute("INSERT INTO visits (mrn, ts, note) VALUES (?,?,?)", (mrn, int(now), note))
        conn.commit()
    return {"mrn": mrn, "ts": int(now), "note": note}


def history(mrn: str) -> List[dict]:
    """A patient's visits, most recent first (the continuity a lost card breaks)."""
    with _lock, _conn() as conn:
        rows = conn.execute("SELECT ts, note FROM visits WHERE mrn=? ORDER BY ts DESC",
                            (mrn,)).fetchall()
    return [{"ts": t, "note": n} for t, n in rows]
