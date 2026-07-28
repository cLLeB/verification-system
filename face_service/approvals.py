"""Generic approval requests with sequential or quorum sign-off.

Lots of privileged actions in the platform should not be self-serve: adding an
admin, exporting a gallery, disabling liveness for a scope, offboarding a tenant.
This subsystem is a reusable maker-checker workflow. A requester opens a request
describing the action; designated approvers vote; the request is granted when the
approval rule is met and denied the moment anyone rejects. It is deliberately
domain-agnostic - the ``action``/``payload`` are opaque, so any caller can gate any
operation behind it.

  * ``open_request``  who wants to do what, plus the approval rule.
  * ``approve`` / ``reject`` - an approver's vote (one vote per approver).
  * ``decision``      current outcome: pending / approved / rejected.
  * ``list_pending``  requests still awaiting a decision, for an approver's queue.

Two rules are supported: ``sequential`` (an ordered list of approvers, each must
sign in turn) and ``quorum`` (any ``threshold`` of an approver set). A single
rejection is final in both - maker-checker fails closed.

Registry: ``approvals.json`` (env ``FACE_APPROVALS_FILE``).
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_APPROVALS_FILE", "approvals.json")


def open_request(tenant: Optional[str], requester: str, action: str,
                 approvers: List[str], rule: str = "quorum", threshold: int = 1,
                 payload: Optional[dict] = None, now: Optional[int] = None) -> dict:
    requester = (requester or "").strip()
    action = (action or "").strip()
    if not requester or not action:
        raise ValueError("requester and action are required.")
    rule = (rule or "").strip().lower()
    if rule not in ("quorum", "sequential"):
        raise ValueError("rule must be 'quorum' or 'sequential'.")
    appr = [a.strip() for a in (approvers or []) if (a or "").strip()]
    if not appr:
        raise ValueError("at least one approver is required.")
    if rule == "quorum":
        threshold = int(threshold)
        if not 1 <= threshold <= len(set(appr)):
            raise ValueError("quorum threshold must be between 1 and #approvers.")
    now = int(now if now is not None else time.time())
    req = {"id": "apr_" + uuid.uuid4().hex[:10], "requester": requester,
           "action": action, "approvers": appr, "rule": rule,
           "threshold": threshold if rule == "quorum" else len(appr),
           "payload": payload or {}, "votes": [], "state": "pending",
           "opened": now}
    with _reg.mutate() as data:
        data.setdefault(_reg.norm(tenant), {})[req["id"]] = req
    return {"id": req["id"], "state": "pending"}


def _resolve(req: dict) -> str:
    votes = req["votes"]
    if any(v["vote"] == "reject" for v in votes):
        return "rejected"
    approved = [v["by"] for v in votes if v["vote"] == "approve"]
    if req["rule"] == "quorum":
        return "approved" if len(set(approved)) >= req["threshold"] else "pending"
    # sequential: approvers must sign strictly in listed order
    for i, expected in enumerate(req["approvers"]):
        if i >= len(approved):
            return "pending"
        if approved[i] != expected:
            return "rejected"       # out-of-order signer breaks the chain
    return "approved"


def _vote(tenant: Optional[str], request_id: str, approver: str, vote: str,
          now: Optional[int]) -> dict:
    approver = (approver or "").strip()
    now = int(now if now is not None else time.time())
    with _reg.mutate() as data:
        req = (data.get(_reg.norm(tenant)) or {}).get((request_id or "").strip())
        if not req:
            return {"ok": False, "reason": "unknown-request"}
        if req["state"] != "pending":
            return {"ok": False, "reason": "already-" + req["state"]}
        if approver not in req["approvers"]:
            return {"ok": False, "reason": "not-an-approver"}
        if any(v["by"] == approver for v in req["votes"]):
            return {"ok": False, "reason": "already-voted"}
        req["votes"].append({"by": approver, "vote": vote, "at": now})
        req["state"] = _resolve(req)
        return {"ok": True, "state": req["state"]}


def approve(tenant: Optional[str], request_id: str, approver: str,
            now: Optional[int] = None) -> dict:
    return _vote(tenant, request_id, approver, "approve", now)


def reject(tenant: Optional[str], request_id: str, approver: str,
           now: Optional[int] = None) -> dict:
    return _vote(tenant, request_id, approver, "reject", now)


def decision(tenant: Optional[str], request_id: str) -> dict:
    req = (_reg.load().get(_reg.norm(tenant)) or {}).get((request_id or "").strip())
    if not req:
        return {"exists": False}
    approved = [v["by"] for v in req["votes"] if v["vote"] == "approve"]
    return {"exists": True, "id": req["id"], "state": req["state"],
            "action": req["action"], "approvals": len(approved),
            "threshold": req["threshold"], "payload": req["payload"]}


def list_pending(tenant: Optional[str], approver: Optional[str] = None) -> List[dict]:
    who = (approver or "").strip()
    out = []
    for req in (_reg.load().get(_reg.norm(tenant)) or {}).values():
        if req["state"] != "pending":
            continue
        if who and who not in req["approvers"]:
            continue
        if who and any(v["by"] == who for v in req["votes"]):
            continue
        out.append({"id": req["id"], "action": req["action"],
                    "requester": req["requester"], "opened": req["opened"]})
    return sorted(out, key=lambda r: r["opened"])
