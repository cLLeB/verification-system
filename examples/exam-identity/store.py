"""The exam board's OWN data: candidate roster + seat check-in log. The backbone
answers "is this really candidate 12345?" (1:1); this file records who checked in,
whether they passed the identity check, and flags impersonation attempts.

Pairs naturally with an exam-integrity/proctoring product (e.g. the Protractor
project): this is the candidate-identity layer at the seat.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import List, Optional

_DB = os.environ.get("EXAM_DB", os.path.join(os.path.dirname(__file__), "exam.db"))
_lock = threading.Lock()

VERIFIED, FLAGGED = "verified", "flagged"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS candidates (
        index_no TEXT PRIMARY KEY, name TEXT, exam TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS checkins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        index_no TEXT, exam TEXT, ts INTEGER, result TEXT, score REAL)""")
    return conn


def register_candidate(index_no: str, name: str, exam: str) -> bool:
    with _lock, _conn() as conn:
        if conn.execute("SELECT 1 FROM candidates WHERE index_no=?", (index_no,)).fetchone():
            return False
        conn.execute("INSERT INTO candidates (index_no, name, exam) VALUES (?,?,?)",
                     (index_no, name, exam))
        conn.commit()
    return True


def record_checkin(index_no: str, exam: str, verified: bool,
                   score: Optional[float] = None, now: Optional[float] = None) -> dict:
    """Log a seat check-in. A failed 1:1 verify is stored as FLAGGED (a candidate
    presenting as someone they don't match - the impersonation signal)."""
    now = now if now is not None else time.time()
    result = VERIFIED if verified else FLAGGED
    with _lock, _conn() as conn:
        conn.execute("INSERT INTO checkins (index_no, exam, ts, result, score) VALUES (?,?,?,?,?)",
                     (index_no, exam, int(now), result, score))
        conn.commit()
    return {"index_no": index_no, "exam": exam, "result": result, "ts": int(now)}


def flagged(exam: Optional[str] = None) -> List[dict]:
    with _lock, _conn() as conn:
        if exam:
            rows = conn.execute("SELECT index_no, ts, score FROM checkins WHERE result=? AND exam=? ORDER BY ts",
                                (FLAGGED, exam)).fetchall()
        else:
            rows = conn.execute("SELECT index_no, ts, score FROM checkins WHERE result=? ORDER BY ts",
                                (FLAGGED,)).fetchall()
    return [{"index_no": i, "ts": t, "score": s} for i, t, s in rows]


def summary(exam: Optional[str] = None) -> dict:
    with _lock, _conn() as conn:
        q, a = "SELECT result, COUNT(*) FROM checkins", ()
        if exam:
            q += " WHERE exam=?"; a = (exam,)
        q += " GROUP BY result"
        counts = dict(conn.execute(q, a).fetchall())
    return {"verified": counts.get(VERIFIED, 0), "flagged": counts.get(FLAGGED, 0)}
