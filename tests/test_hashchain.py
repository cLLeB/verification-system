"""Hash chain: append-only ledger with tamper detection."""

from __future__ import annotations

import os

import pytest

from face_service import hashchain

T = "t_chain_test"


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    os.environ["FACE_HASHCHAIN_FILE"] = str(tmp_path / "chain.json")
    yield


def test_append_links_and_verifies():
    hashchain.append(T, "door_open", {"user": "ama"})
    hashchain.append(T, "door_open", {"user": "kofi"})
    assert hashchain.is_intact(T)
    assert hashchain.head(T) == hashchain.entries(T)[-1]["hash"]


def test_tamper_is_detected():
    hashchain.append(T, "a", {"n": 1})
    hashchain.append(T, "b", {"n": 2})
    hashchain.append(T, "c", {"n": 3})
    # tamper with the middle entry directly in the store
    data = hashchain._reg.load()
    data[hashchain._reg.norm(T)][1]["payload"]["n"] = 999
    hashchain._reg.save(data)
    break_at = hashchain.verify(T)
    assert break_at is not None and break_at["seq"] == 1


def test_reorder_detected():
    hashchain.append(T, "a")
    hashchain.append(T, "b")
    data = hashchain._reg.load()
    key = hashchain._reg.norm(T)
    data[key][0], data[key][1] = data[key][1], data[key][0]
    hashchain._reg.save(data)
    assert not hashchain.is_intact(T)


def test_empty_chain_intact():
    assert hashchain.is_intact(T)
    assert hashchain.head(T) == hashchain.GENESIS
