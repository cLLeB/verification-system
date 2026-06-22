"""In-process request metrics, exposed in Prometheus text format at /metrics.

Low-cardinality by design: it keys on the Flask *endpoint* name (not the raw URL)
and the status code, so the series count stays bounded. Good enough to drive a
dashboard/alerts for a single-worker deployment; swap for prometheus_client if you
run multiple workers.
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_counts: dict = {}                       # (endpoint, status) -> count
_latency: dict = {}                      # endpoint -> [sum_seconds, count]
_started = time.time()


def observe(endpoint: str, status: int, seconds: float) -> None:
    endpoint = endpoint or "unknown"
    with _lock:
        k = (endpoint, int(status))
        _counts[k] = _counts.get(k, 0) + 1
        s = _latency.setdefault(endpoint, [0.0, 0])
        s[0] += seconds
        s[1] += 1


def render() -> str:
    lines = [
        "# HELP face_requests_total Total HTTP requests by endpoint and status.",
        "# TYPE face_requests_total counter",
    ]
    with _lock:
        for (endpoint, status), n in sorted(_counts.items()):
            lines.append(f'face_requests_total{{endpoint="{endpoint}",status="{status}"}} {n}')
        lines.append("# HELP face_request_latency_seconds_avg Mean request latency per endpoint.")
        lines.append("# TYPE face_request_latency_seconds_avg gauge")
        for endpoint, (total, count) in sorted(_latency.items()):
            avg = total / count if count else 0.0
            lines.append(f'face_request_latency_seconds_avg{{endpoint="{endpoint}"}} {avg:.6f}')
        lines.append("# HELP face_uptime_seconds Seconds since process start.")
        lines.append("# TYPE face_uptime_seconds gauge")
        lines.append(f"face_uptime_seconds {time.time() - _started:.0f}")
    return "\n".join(lines) + "\n"
