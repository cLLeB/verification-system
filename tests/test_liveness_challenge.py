"""Active-liveness challenge tokens: unique, signed, single-use (anti-replay).

Imports the face engine module, so it skips where models/cv2 are absent (run via the
venv). The logic under test is pure HMAC + a nonce set - no model inference needed.
"""

import time

import pytest

try:
    from face import liveness_active as la
except Exception as exc:  # pragma: no cover - engine deps absent
    pytest.skip(f"face engine unavailable: {exc}", allow_module_level=True)


def test_tokens_are_unique_and_three_part():
    a = la.new_challenge()["token"]
    b = la.new_challenge()["token"]
    assert a != b                       # random nonce -> no two identical tokens
    assert len(a.split(".")) == 3       # exp.nonce.sig


def test_valid_token_is_single_use():
    tok = la.new_challenge()["token"]
    assert la.valid_token(tok) is True      # first use accepted
    assert la.valid_token(tok) is False     # replay of the same token rejected


def test_consume_false_does_not_burn():
    tok = la.new_challenge()["token"]
    assert la.valid_token(tok, consume=False) is True
    assert la.valid_token(tok, consume=False) is True   # not consumed
    assert la.valid_token(tok) is True                  # still usable once
    assert la.valid_token(tok) is False                 # now burned


def test_rejects_tampered_signature():
    tok = la.new_challenge()["token"]
    exp, nonce, _sig = tok.split(".")
    assert la.valid_token(f"{exp}.{nonce}.deadbeefdeadbeef") is False


def test_rejects_expired_even_if_signed():
    past = int(time.time()) - 10
    nonce = "abcdef0123456789"
    tok = f"{past}.{nonce}.{la._sign(past, nonce)}"     # correctly signed but expired
    assert la.valid_token(tok) is False


def test_rejects_malformed():
    for bad in ("", "garbage", "1.2", "a.b.c.d", None):
        assert la.valid_token(bad) is False
