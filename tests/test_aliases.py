"""Identity aliases: canonical resolution, chain collapse, cycle refusal."""

from __future__ import annotations

import os

import pytest

from face_service import aliases

T = "t_alias_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_ALIASES_FILE"] = str(tmp_path / "aliases.json")
    yield


def test_resolve_default_is_self():
    assert aliases.resolve(T, "ama") == "ama"


def test_link_and_resolve():
    aliases.link(T, "emp-42", "ama")
    assert aliases.resolve(T, "emp-42") == "ama"
    assert aliases.aliases_of(T, "ama") == ["emp-42"]


def test_chain_collapses():
    aliases.link(T, "b", "a")
    aliases.link(T, "c", "b")            # should collapse to a
    assert aliases.resolve(T, "c") == "a"
    assert set(aliases.aliases_of(T, "a")) == {"b", "c"}


def test_cycle_refused():
    aliases.link(T, "b", "a")
    with pytest.raises(ValueError):
        aliases.link(T, "a", "b")


def test_unlink():
    aliases.link(T, "emp-42", "ama")
    assert aliases.unlink(T, "emp-42")
    assert not aliases.unlink(T, "emp-42")
    assert aliases.resolve(T, "emp-42") == "emp-42"


def test_validation():
    with pytest.raises(ValueError):
        aliases.link(T, "a", "a")
