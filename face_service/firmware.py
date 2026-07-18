"""Device firmware registry — track versions and flag vulnerable/outdated readers.

A fleet of edge capture devices runs firmware that drifts out of date and
occasionally ships with a known vulnerability. Security teams need to answer, at any
moment, "which readers are behind the required baseline or running a version we've
flagged as vulnerable?" This subsystem is that inventory: register each device's
reported version, declare a required minimum and a set of known-bad versions, and
compute per-device compliance.

  * ``report_version``  a device checks in with its firmware version.
  * ``set_baseline``    the minimum acceptable version for a device model.
  * ``flag_vulnerable`` mark specific versions as known-vulnerable (with a note).
  * ``check``           one device: up-to-date? vulnerable? below baseline?
  * ``fleet``           roll-up counts and the list of non-compliant devices.

Versions are compared with dotted-numeric semantics (``1.10.0`` > ``1.9.9``), so
lexical pitfalls don't cause false compliance. A device with no baseline for its
model is considered compliant on the baseline axis but still checked for vulns.

Registry: ``firmware.json`` (env ``FACE_FIRMWARE_FILE``).
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from ._registry import Registry

_reg = Registry("FACE_FIRMWARE_FILE", "firmware.json")


def _parse(v: str) -> Tuple[int, ...]:
    parts = []
    for chunk in str(v or "").strip().split("."):
        num = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts) or (0,)


def _cmp(a: str, b: str) -> int:
    pa, pb = _parse(a), _parse(b)
    width = max(len(pa), len(pb))
    pa += (0,) * (width - len(pa))
    pb += (0,) * (width - len(pb))
    return (pa > pb) - (pa < pb)


def _root(data: dict, tenant: Optional[str]) -> dict:
    return data.setdefault(_reg.norm(tenant),
                           {"devices": {}, "baselines": {}, "vulns": {}})


def report_version(tenant: Optional[str], device: str, version: str,
                   model: str = "", now: Optional[int] = None) -> dict:
    device = (device or "").strip()
    version = (version or "").strip()
    if not device or not version:
        raise ValueError("device and version are required.")
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        _root(data, tenant)["devices"][device] = {
            "device": device, "version": version,
            "model": (model or "").strip(), "reported": now}
    return {"device": device, "version": version}


def set_baseline(tenant: Optional[str], model: str, min_version: str) -> dict:
    model = (model or "").strip()
    if not model or not (min_version or "").strip():
        raise ValueError("model and min_version are required.")
    with _reg.mutate() as data:
        _root(data, tenant)["baselines"][model] = (min_version or "").strip()
    return {"model": model, "min_version": min_version}


def flag_vulnerable(tenant: Optional[str], version: str, note: str = "",
                    model: str = "") -> dict:
    version = (version or "").strip()
    if not version:
        raise ValueError("version is required.")
    key = f"{(model or '').strip()}|{version}"
    with _reg.mutate() as data:
        _root(data, tenant)["vulns"][key] = {"version": version,
                                             "model": (model or "").strip(),
                                             "note": (note or "").strip()}
    return {"version": version, "model": model or None}


def check(tenant: Optional[str], device: str) -> dict:
    root = _reg.load().get(_reg.norm(tenant)) or {}
    dev = (root.get("devices") or {}).get((device or "").strip())
    if not dev:
        return {"exists": False}
    model, ver = dev["model"], dev["version"]
    baseline = (root.get("baselines") or {}).get(model)
    below = bool(baseline) and _cmp(ver, baseline) < 0
    vulns = root.get("vulns") or {}
    vuln_hit = vulns.get(f"{model}|{ver}") or vulns.get(f"|{ver}")
    return {"exists": True, "device": dev["device"], "version": ver, "model": model,
            "baseline": baseline, "below_baseline": below,
            "vulnerable": bool(vuln_hit),
            "vuln_note": (vuln_hit or {}).get("note"),
            "compliant": not below and not vuln_hit}


def fleet(tenant: Optional[str]) -> dict:
    root = _reg.load().get(_reg.norm(tenant)) or {}
    devices = (root.get("devices") or {})
    non_compliant = []
    for dev in devices:
        c = check(tenant, dev)
        if not c["compliant"]:
            non_compliant.append({"device": dev, "version": c["version"],
                                  "below_baseline": c["below_baseline"],
                                  "vulnerable": c["vulnerable"]})
    return {"total": len(devices), "non_compliant": len(non_compliant),
            "compliant": len(devices) - len(non_compliant),
            "devices": sorted(non_compliant, key=lambda d: d["device"])}
