"""Password hashing: verify, salt uniqueness, rehash detection, strength."""

from __future__ import annotations

import pytest

from face_service import passwords as pw


def test_hash_and_verify():
    h = pw.hash_password("correct horse battery staple", iterations=2000)
    assert pw.verify("correct horse battery staple", h)
    assert not pw.verify("wrong password", h)


def test_format():
    h = pw.hash_password("secret", iterations=2000)
    assert h.startswith("pbkdf2_sha256$2000$") and h.count("$") == 3


def test_salt_makes_hashes_unique():
    a = pw.hash_password("same", iterations=2000)
    b = pw.hash_password("same", iterations=2000)
    assert a != b
    assert pw.verify("same", a) and pw.verify("same", b)


def test_needs_rehash_on_low_iterations():
    h = pw.hash_password("secret", iterations=2000)
    assert pw.needs_rehash(h, iterations=210000)
    assert not pw.needs_rehash(pw.hash_password("secret", iterations=210000),
                               iterations=210000)


def test_needs_rehash_on_bad_format():
    assert pw.needs_rehash("not-a-valid-hash")


def test_verify_malformed_returns_false():
    assert not pw.verify("x", "garbage")
    assert not pw.verify("x", "")


def test_strength_scoring():
    assert pw.strength("abc")["label"] in ("very-weak", "weak")
    strong = pw.strength("Abcdef123!@#XYZ")
    assert strong["score"] >= 3 and strong["classes"] == 4


def test_validation():
    with pytest.raises(ValueError):
        pw.hash_password("")
    with pytest.raises(ValueError):
        pw.hash_password("x", iterations=10)
