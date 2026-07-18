"""Sealbox authenticated encryption: roundtrip, tamper detection, AAD, rewrap."""

from __future__ import annotations

import base64

import pytest

from face_service import sealbox as ev


def test_roundtrip():
    token = ev.seal("master-key", b"secret template bytes")
    assert ev.open("master-key", token) == b"secret template bytes"


def test_string_plaintext():
    token = ev.seal("k", "hello world")
    assert ev.open("k", token) == b"hello world"


def test_wrong_key_fails():
    token = ev.seal("k1", b"data")
    with pytest.raises(ValueError):
        ev.open("k2", token)


def test_tampered_ciphertext_fails():
    token = ev.seal("k", b"data")
    ct = bytearray(base64.b64decode(token["ct"]))
    ct[0] ^= 0x01
    token["ct"] = base64.b64encode(bytes(ct)).decode()
    with pytest.raises(ValueError):
        ev.open("k", token)


def test_aad_must_match():
    token = ev.seal("k", b"data", aad=b"context-A")
    assert ev.open("k", token, aad=b"context-A") == b"data"
    with pytest.raises(ValueError):
        ev.open("k", token, aad=b"context-B")


def test_nonce_is_random_per_seal():
    a = ev.seal("k", b"data")
    b = ev.seal("k", b"data")
    assert a["nonce"] != b["nonce"] and a["ct"] != b["ct"]


def test_rewrap_rotates_key():
    token = ev.seal("old", b"payload")
    rewrapped = ev.rewrap("old", "new", token)
    assert ev.open("new", rewrapped) == b"payload"
    with pytest.raises(ValueError):
        ev.open("old", rewrapped)


def test_empty_plaintext_ok():
    token = ev.seal("k", b"")
    assert ev.open("k", token) == b""


def test_validation():
    with pytest.raises(ValueError):
        ev.seal("", b"x")
    with pytest.raises(ValueError):
        ev.open("k", {"nonce": "!!", "ct": "!!", "tag": "!!"})
