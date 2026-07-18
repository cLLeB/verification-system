"""Registry hardening: atomic writes, corrupt recovery, locking, safe keys."""

from __future__ import annotations

import glob
import json
import os
import threading

import pytest

from face_service._registry import Registry


@pytest.fixture
def reg(tmp_path, monkeypatch):
    path = tmp_path / "store.json"
    monkeypatch.setenv("FACE_TEST_REGISTRY_FILE", str(path))
    return Registry("FACE_TEST_REGISTRY_FILE", str(path)), str(path)


def test_roundtrip(reg):
    r, path = reg
    r.save({"a": 1})
    assert r.load() == {"a": 1}


def test_atomic_write_leaves_no_temp(reg):
    r, path = reg
    with r.mutate() as d:
        d["x"] = 1
    # no .tmp.* residue after a clean write
    assert glob.glob(path + ".tmp.*") == []


def test_corrupt_file_is_preserved_not_silently_lost(reg):
    r, path = reg
    r.save({"real": "data"})
    with open(path, "w", encoding="utf-8") as fh:      # simulate truncated write
        fh.write('{ "real": {corrupt')
    # load must NOT crash, must return {}, and must move the bad file aside
    assert r.load() == {}
    backups = glob.glob(path + ".corrupt.*")
    assert backups, "corrupt file should be preserved for recovery"
    assert '{corrupt' in open(backups[0], encoding="utf-8").read()
    # store is usable again afterwards
    r.save({"fresh": 1})
    assert r.load() == {"fresh": 1}


def test_concurrent_mutations_do_not_lose_updates(reg):
    r, path = reg
    r.save({"counter": 0})

    def bump():
        for _ in range(50):
            with r.mutate() as d:
                d["counter"] = d.get("counter", 0) + 1

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 4 threads * 50 increments, none lost (in-process lock serialises them)
    assert r.load()["counter"] == 200


def test_scoped_keys_do_not_collide():
    a = Registry.scoped("a", "x:evil")
    b = Registry.scoped("a:x", "evil")
    assert a != b                       # the exact multi-tenant collision, now prevented
    # and they remain deterministic
    assert Registry.scoped("t", "s") == Registry.scoped("t", "s")


def test_scoped_escapes_backslash_too():
    assert Registry.scoped("a\\", "b") != Registry.scoped("a", "\\b")


def test_norm_defaults():
    assert Registry.norm(None) == "default"
    assert Registry.norm("  ") == "default"
    assert Registry.norm(" t ") == "t"
