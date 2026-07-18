"""JSON diff/patch — compute and apply structural changes to config documents.

Config lives in JSON-shaped documents (policies, thresholds, branding). Auditing and
rolling back changes needs a precise, replayable description of *what* changed — not a
whole-document snapshot. This subsystem computes a minimal patch between two documents and
applies patches, in the spirit of RFC 6902 JSON Patch. It pairs with [[eventlog]] (store
the patch as the change record) and config versioning.

  * ``diff``    the ops turning document ``a`` into ``b``: ``add`` / ``remove`` /
                ``replace`` with a JSON-Pointer-style ``path``.
  * ``apply``   apply a patch to a document, returning a new document (immutable — the
                input is never mutated).
  * ``invert``  the inverse patch (needs the original doc) for one-click rollback.

Paths are ``/``-separated pointers (``/thresholds/lobby``). Objects diff key-by-key;
lists are compared positionally (replaced element-wise, with add/remove for length
changes) — simple and predictable rather than a minimal-edit list diff.
"""

from __future__ import annotations

import copy
from typing import List, Optional


def _esc(token: str) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def _join(prefix: str, token) -> str:
    return f"{prefix}/{_esc(token)}"


def diff(a, b, _path: str = "") -> List[dict]:
    ops: List[dict] = []
    if isinstance(a, dict) and isinstance(b, dict):
        for key in a:
            if key not in b:
                ops.append({"op": "remove", "path": _join(_path, key)})
            else:
                ops.extend(diff(a[key], b[key], _join(_path, key)))
        for key in b:
            if key not in a:
                ops.append({"op": "add", "path": _join(_path, key), "value": copy.deepcopy(b[key])})
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(min(len(a), len(b))):
            ops.extend(diff(a[i], b[i], _join(_path, i)))
        for i in range(len(a) - 1, len(b) - 1, -1):        # removals, high index first
            ops.append({"op": "remove", "path": _join(_path, i)})
        for i in range(len(a), len(b)):
            ops.append({"op": "add", "path": _join(_path, i), "value": copy.deepcopy(b[i])})
    else:
        if a != b:
            ops.append({"op": "replace", "path": _path or "/", "value": copy.deepcopy(b)})
    return ops


def _tokens(path: str) -> List[str]:
    if path in ("", "/"):
        return []
    return [t.replace("~1", "/").replace("~0", "~") for t in path.split("/")[1:]]


def _get_parent(doc, tokens):
    node = doc
    for t in tokens[:-1]:
        if isinstance(node, list):
            node = node[int(t)]
        else:
            node = node[t]
    return node


def apply(doc, ops: List[dict]):
    result = copy.deepcopy(doc)
    for op in ops:
        tokens = _tokens(op["path"])
        kind = op["op"]
        if not tokens:                       # whole-document replace
            if kind == "replace":
                result = copy.deepcopy(op["value"])
            continue
        parent = _get_parent(result, tokens)
        last = tokens[-1]
        if isinstance(parent, list):
            idx = int(last)
            if kind == "add":
                parent.insert(idx, copy.deepcopy(op["value"]))
            elif kind == "remove":
                del parent[idx]
            elif kind == "replace":
                parent[idx] = copy.deepcopy(op["value"])
        else:
            if kind == "add" or kind == "replace":
                parent[last] = copy.deepcopy(op["value"])
            elif kind == "remove":
                parent.pop(last, None)
    return result


def invert(original, ops: List[dict]) -> List[dict]:
    """Produce the patch that undoes `ops` when applied to apply(original, ops)."""
    return diff(apply(original, ops), original)
