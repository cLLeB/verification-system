"""Credential core: base45 RFC vectors, quantization accuracy, build/sign/verify
round-trip, tamper/expiry/unknown-issuer fail-closed, live-capture matching."""
import numpy as np
import pytest

from biometric.core import base45, credential, envelope, protect, signing
from biometric.core.base45 import Base45Error
from biometric.core.credential import CredentialError


def _unit(dim=512, seed=0):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


# --- base45 (RFC 9285 test vectors) ------------------------------------------
def test_base45_rfc_vectors():
    assert base45.encode(b"AB") == "BB8"
    assert base45.encode(b"Hello!!") == "%69 VD92EX0"
    assert base45.encode(b"base-45") == "UJCLQE7W581"
    assert base45.decode("QED8WEX0") == b"ietf!"


def test_base45_round_trip_and_strictness():
    import os
    for n in (0, 1, 2, 31, 64):
        blob = os.urandom(n)
        assert base45.decode(base45.encode(blob)) == blob
    with pytest.raises(Base45Error):
        base45.decode("A")                     # length % 3 == 1
    with pytest.raises(Base45Error):
        base45.decode("ab")                    # lowercase not in alphabet
    with pytest.raises(Base45Error):
        base45.decode("ZZZ")                   # triple overflows 0xFFFF


# --- quantization -------------------------------------------------------------
def test_quantize_round_trip_is_near_lossless():
    v = _unit(seed=1)
    back = credential.dequantize(credential.quantize(v))
    assert float(v @ back) > 0.9995


# --- build / sign / verify -----------------------------------------------------
def _issue(sk, pk, cid=None, **kw):
    cid = cid or credential.new_cid()
    tpl = credential.template_envelope(cid, "face", _unit(seed=2))
    payload = credential.build(cid, "acme", signing.kid(pk), "alice",
                               [tpl], ["face"], **kw)
    sig = signing.sign(sk, credential.signing_bytes(payload))
    return credential.encode(payload, sig), cid


def test_round_trip_verify_and_match():
    sk, pk = signing.generate()
    text, cid = _issue(sk, pk)
    assert text.startswith("FV1:")
    payload = credential.verify(text, lambda iss, kid: pk)
    assert payload["sub"] == "alice" and payload["cid"] == cid
    # the enrolled person matches; a stranger does not
    assert credential.match(payload, "face", _unit(seed=2)) > 0.99
    assert credential.match(payload, "face", _unit(seed=3)) < 0.3


def test_credential_domain_is_unlinkable_to_store_and_other_credentials():
    sk, pk = signing.generate()
    raw = _unit(seed=4)
    cid1, cid2 = credential.new_cid(), credential.new_cid()
    t1 = credential.dequantize(
        envelope.decode(credential.template_envelope(cid1, "face", raw))["data"])
    t2 = credential.dequantize(
        envelope.decode(credential.template_envelope(cid2, "face", raw))["data"])
    assert abs(float(t1 @ t2)) < 0.3           # two credentials never cross-match
    store_domain = protect.transform(b"\x01" * 32, raw)[0]
    assert abs(float(t1 @ store_domain)) < 0.3  # nor match any store domain


def test_tamper_and_wrong_key_fail_closed():
    sk, pk = signing.generate()
    text, _ = _issue(sk, pk)
    _, other_pk = signing.generate()
    with pytest.raises(CredentialError) as e:
        credential.verify(text, lambda iss, kid: other_pk)
    assert e.value.code == "bad_signature"
    # bit-flip anywhere in the encoded payload
    body = text[4:]
    flipped = "FV1:" + ("0" if body[5] != "0" else "1").join((body[:5], body[6:]))
    with pytest.raises(CredentialError):
        credential.verify(flipped, lambda iss, kid: pk)


def test_expiry_unknown_issuer_and_version():
    sk, pk = signing.generate()
    text, _ = _issue(sk, pk, expiry_days=1)
    payload, _pbytes, _sig = credential.decode(text)
    with pytest.raises(CredentialError) as e:
        credential.verify(text, lambda iss, kid: pk, now=payload["exp"] + 1)
    assert e.value.code == "credential_expired"
    with pytest.raises(CredentialError) as e:
        credential.verify(text, lambda iss, kid: None)
    assert e.value.code == "unknown_issuer"
    with pytest.raises(CredentialError) as e:
        credential.decode("HC1:XYZ")
    assert e.value.code == "malformed_credential"


def test_qr_size_budget():
    """Payload must comfortably fit a QR (spec 6.1: ~1.2 KB at version-25 ECC-M)."""
    sk, pk = signing.generate()
    text, _ = _issue(sk, pk)
    assert len(text) < 1900                    # alphanumeric chars, incl. face template
