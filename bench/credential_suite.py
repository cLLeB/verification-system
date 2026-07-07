"""Credential suite: integrity, quantization cost, size budget, crypto speed.

Dataset-free (synthetic unit embeddings) — measures the exact issue/verify code
paths from ``biometric.core.credential``.
"""

from __future__ import annotations

import time

import numpy as np

from biometric.core import credential, signing


def _units(n: int, dim: int, rng) -> np.ndarray:
    v = rng.standard_normal((n, dim)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def run(n: int = 200, dim: int = 512) -> dict:
    rng = np.random.default_rng(11)
    raws = _units(n, dim, rng)
    sk, pk = signing.generate()
    kid = signing.kid(pk)

    sizes, quant_cos, verify_ms, match_scores, stranger_scores = [], [], [], [], []
    issue_ms = []
    stranger = _units(1, dim, rng)[0]
    for i in range(n):
        t0 = time.perf_counter()
        cid = credential.new_cid()
        tpl = credential.template_envelope(cid, "face", raws[i])
        payload = credential.build(cid, "bench", kid, f"u{i}", [tpl], ["face"])
        sig = signing.sign(sk, credential.signing_bytes(payload))
        text = credential.encode(payload, sig)
        issue_ms.append((time.perf_counter() - t0) * 1000)
        sizes.append(len(text))

        t0 = time.perf_counter()
        p = credential.verify(text, lambda iss, k: pk)
        verify_ms.append((time.perf_counter() - t0) * 1000)
        match_scores.append(credential.match(p, "face", raws[i]))
        stranger_scores.append(credential.match(p, "face", stranger))
        # quantization cost in isolation
        quant_cos.append(float(
            credential.dequantize(credential.quantize(raws[i])) @ raws[i]))

    def stats(xs):
        a = np.asarray(xs, np.float64)
        return {"min": round(float(a.min()), 4), "mean": round(float(a.mean()), 4),
                "max": round(float(a.max()), 4)}

    holder_min = float(np.min(match_scores))
    stranger_max = float(np.max(np.abs(stranger_scores)))
    return {
        "date": time.strftime("%Y-%m-%d"),
        "credentials": n,
        "wire_chars": stats(sizes),
        "fits_qr_v25_eccm": bool(max(sizes) <= 1852),   # alphanumeric capacity
        "quantization_cosine": stats(quant_cos),
        "holder_match_score": stats(match_scores),
        "stranger_score_abs_max": round(stranger_max, 4),
        "issue_ms": stats(issue_ms),
        "verify_ms": stats(verify_ms),
        "gate": "PASS" if (holder_min > 0.98 and stranger_max < 0.3
                           and max(sizes) <= 1852) else "FAIL",
    }
