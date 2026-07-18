"""Directed acyclic graphs — order dependent work and detect cycles.

Several parts of the platform have ordering constraints: schema [[migrations]] that depend
on earlier ones, policy rules that reference others, an onboarding checklist of prerequisite
steps. A DAG models "X must happen before Y" and yields a valid execution order — or flags an
impossible cycle. This subsystem is a small persisted dependency graph with topological
sort, cycle detection, and ancestor/descendant queries.

  * ``add_edge``     declare that ``node`` depends on ``prerequisite`` (edge pre → node).
  * ``topo_order``   a valid ordering where prerequisites come first; errors on a cycle.
  * ``find_cycle``   return a cycle if one exists (for diagnostics), else ``None``.
  * ``ancestors`` / ``descendants`` — transitive prerequisites / dependents of a node.
  * ``roots`` / ``leaves`` — nodes with no prerequisites / no dependents.

Topological sort uses Kahn's algorithm (deterministic: ties broken by node name), so the
order is stable across runs. Adding an edge that would create a cycle is allowed at store
time but surfaced by ``topo_order``/``find_cycle`` so callers can report the exact loop.

Registry: ``dag.json`` (env ``FACE_DAG_FILE``).
"""

from __future__ import annotations

from typing import List, Optional

from ._registry import Registry

_reg = Registry("FACE_DAG_FILE", "dag.json")


def _graph(data: dict, tenant: Optional[str]) -> dict:
    # adjacency: node -> list of prerequisites
    return data.setdefault(_reg.norm(tenant), {"deps": {}})


def add_node(tenant: Optional[str], node: str) -> dict:
    node = (node or "").strip()
    if not node:
        raise ValueError("node is required.")
    with _reg.mutate() as data:
        _graph(data, tenant)["deps"].setdefault(node, [])
    return {"node": node}


def add_edge(tenant: Optional[str], node: str, prerequisite: str) -> dict:
    node = (node or "").strip()
    prerequisite = (prerequisite or "").strip()
    if not node or not prerequisite:
        raise ValueError("node and prerequisite are required.")
    if node == prerequisite:
        raise ValueError("a node cannot depend on itself.")
    with _reg.mutate() as data:
        deps = _graph(data, tenant)["deps"]
        deps.setdefault(prerequisite, [])
        lst = deps.setdefault(node, [])
        if prerequisite not in lst:
            lst.append(prerequisite)
    return {"node": node, "prerequisite": prerequisite}


def _deps(tenant: Optional[str]) -> dict:
    return (_reg.load().get(_reg.norm(tenant)) or {"deps": {}})["deps"]


def find_cycle(tenant: Optional[str]) -> Optional[List[str]]:
    deps = _deps(tenant)
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in deps}
    stack: List[str] = []

    def dfs(n: str) -> Optional[List[str]]:
        color[n] = GREY
        stack.append(n)
        for pre in sorted(deps.get(n, [])):
            if color.get(pre, WHITE) == GREY:
                return stack[stack.index(pre):] + [pre]
            if color.get(pre, WHITE) == WHITE:
                found = dfs(pre)
                if found:
                    return found
        color[n] = BLACK
        stack.pop()
        return None

    for node in sorted(deps):
        if color[node] == WHITE:
            found = dfs(node)
            if found:
                return found
    return None


def topo_order(tenant: Optional[str]) -> List[str]:
    deps = _deps(tenant)
    # Kahn's algorithm on edges prerequisite -> node
    indeg = {n: 0 for n in deps}
    adj = {n: [] for n in deps}
    for node, prereqs in deps.items():
        for pre in prereqs:
            adj.setdefault(pre, []).append(node)
            indeg[node] = indeg.get(node, 0) + 1
            indeg.setdefault(pre, indeg.get(pre, 0))
    ready = sorted(n for n, d in indeg.items() if d == 0)
    order: List[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in sorted(adj.get(n, [])):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
                ready.sort()
    if len(order) != len(indeg):
        cycle = find_cycle(tenant)
        raise ValueError(f"graph has a cycle: {cycle}")
    return order


def _transitive(tenant: Optional[str], start: str, forward: bool) -> List[str]:
    deps = _deps(tenant)
    if forward:
        adj = deps                                # node -> prerequisites
    else:
        adj = {n: [] for n in deps}
        for node, prereqs in deps.items():
            for pre in prereqs:
                adj.setdefault(pre, []).append(node)
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return sorted(seen)


def ancestors(tenant: Optional[str], node: str) -> List[str]:
    return _transitive(tenant, (node or "").strip(), forward=True)


def descendants(tenant: Optional[str], node: str) -> List[str]:
    return _transitive(tenant, (node or "").strip(), forward=False)


def roots(tenant: Optional[str]) -> List[str]:
    deps = _deps(tenant)
    return sorted(n for n, p in deps.items() if not p)


def leaves(tenant: Optional[str]) -> List[str]:
    deps = _deps(tenant)
    has_dependents = {pre for prereqs in deps.values() for pre in prereqs}
    return sorted(n for n in deps if n not in has_dependents)
