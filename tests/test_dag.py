"""DAG: topological order, cycle detection, ancestors/descendants."""

from __future__ import annotations

import os

import pytest

from face_service import dag

T = "t_dag_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_DAG_FILE"] = str(tmp_path / "dag.json")
    yield


def test_topo_order_respects_dependencies():
    # c depends on b depends on a
    dag.add_edge(T, "b", "a")
    dag.add_edge(T, "c", "b")
    order = dag.topo_order(T)
    assert order.index("a") < order.index("b") < order.index("c")


def test_topo_order_deterministic():
    dag.add_edge(T, "x", "a")
    dag.add_edge(T, "y", "a")
    assert dag.topo_order(T) == ["a", "x", "y"]   # ties by name


def test_cycle_detected():
    dag.add_edge(T, "b", "a")
    dag.add_edge(T, "a", "b")     # cycle
    assert dag.find_cycle(T) is not None
    with pytest.raises(ValueError):
        dag.topo_order(T)


def test_no_cycle():
    dag.add_edge(T, "b", "a")
    assert dag.find_cycle(T) is None


def test_ancestors_and_descendants():
    dag.add_edge(T, "b", "a")
    dag.add_edge(T, "c", "b")
    assert dag.ancestors(T, "c") == ["a", "b"]
    assert dag.descendants(T, "a") == ["b", "c"]


def test_roots_and_leaves():
    dag.add_edge(T, "b", "a")
    dag.add_edge(T, "c", "b")
    assert dag.roots(T) == ["a"]
    assert dag.leaves(T) == ["c"]


def test_self_dependency_rejected():
    with pytest.raises(ValueError):
        dag.add_edge(T, "a", "a")


def test_diamond():
    #   a -> b,c -> d
    dag.add_edge(T, "b", "a")
    dag.add_edge(T, "c", "a")
    dag.add_edge(T, "d", "b")
    dag.add_edge(T, "d", "c")
    order = dag.topo_order(T)
    assert order[0] == "a" and order[-1] == "d"


def test_validation():
    with pytest.raises(ValueError):
        dag.add_node(T, "")
    with pytest.raises(ValueError):
        dag.add_edge(T, "a", "")
