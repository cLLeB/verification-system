"""SourceAFIS backend (gold-standard fingerprint matcher) via JPype/JVM.

SourceAFIS does its own segmentation, enhancement, minutiae extraction and
matching internally from a grayscale image. We feed it the contrast-equalised
ROI; it returns an open-ended similarity score (~40 is its standard match
threshold). Used alongside our minutiae matcher in a fusion decision.

If the jars or a JVM are unavailable, `available()` returns False and the engine
falls back to the minutiae matcher alone.
"""

from __future__ import annotations

import glob
import os
import threading

import numpy as np

_LIBS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "libs")
_lock = threading.Lock()          # guards one-time JVM init
_compute_lock = threading.RLock()  # serialises JVM calls (avoids JPype+threads hangs)
_state = {"available": None}
_cls = {}


def _ensure() -> bool:
    if _state["available"] is not None:
        return _state["available"]
    with _lock:
        if _state["available"] is not None:
            return _state["available"]
        try:
            import jpype
            import jpype.imports  # noqa: F401
            jars = glob.glob(os.path.join(_LIBS, "*.jar"))
            if not jars:
                _state["available"] = False
                return False
            if not jpype.isJVMStarted():
                jpype.startJVM(classpath=jars, convertStrings=False)
            from com.machinezoo.sourceafis import (  # type: ignore
                FingerprintImage, FingerprintImageOptions,
                FingerprintMatcher, FingerprintTemplate,
            )
            _cls.update(
                Image=FingerprintImage, Options=FingerprintImageOptions,
                Matcher=FingerprintMatcher, Template=FingerprintTemplate,
                jpype=jpype,
            )
            _state["available"] = True
        except Exception:
            _state["available"] = False
    return _state["available"]


def available() -> bool:
    return _ensure()


def _attach():
    """Attach the current thread to the JVM (Flask serves requests on threads;
    JPype requires each Java-calling thread to be attached, or it crashes)."""
    jpype = _cls["jpype"]
    if not jpype.isThreadAttachedToJVM():
        jpype.attachThreadToJVM()


def build_template(gray_uint8: np.ndarray, dpi: float = 500.0) -> bytes:
    """Grayscale image -> serialized SourceAFIS template bytes (b'' if unavailable)."""
    if not _ensure():
        return b""
    with _compute_lock:
        _attach()
        jpype = _cls["jpype"]
        g = np.ascontiguousarray(gray_uint8.astype(np.uint8))
        h, w = g.shape
        jb = jpype.JArray(jpype.JByte)(g.reshape(-1).astype(np.int8))
        img = _cls["Image"](int(w), int(h), jb, _cls["Options"]().dpi(float(dpi)))
        tmpl = _cls["Template"](img)
        return bytes(tmpl.toByteArray())


def score(probe_bytes: bytes, candidate_bytes: bytes) -> float:
    """Similarity between two serialized templates (0.0 if unavailable/empty)."""
    if not _ensure() or not probe_bytes or not candidate_bytes:
        return 0.0
    with _compute_lock:
        _attach()
        jpype = _cls["jpype"]
        Template = _cls["Template"]
        p = Template(jpype.JArray(jpype.JByte)(bytearray(probe_bytes)))
        c = Template(jpype.JArray(jpype.JByte)(bytearray(candidate_bytes)))
        return float(_cls["Matcher"](p).match(c))
