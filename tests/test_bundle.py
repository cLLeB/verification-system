"""Offline provisioning bundle: passphrase-encrypted, integrity-protected template
export for air-gapped devices. Pure-crypto tests (no models/flask needed)."""

import pytest

from face_service import bundle

pytestmark = pytest.mark.skipif(not bundle.available(),
                                reason="cryptography library unavailable")


def _payload():
    return bundle.build_payload(
        "acme",
        face=[{"user_id": "Kofi Mensah", "embeddings": [[0.1, 0.2, 0.3]]}],
        palm=[{"user_id": "Kofi Mensah", "embeddings": [[0.4, 0.5]]}],
    )


def test_roundtrip_preserves_payload():
    b = bundle.pack(_payload(), "correct horse battery")
    got = bundle.unpack(b, "correct horse battery")
    assert got["format"] == bundle.PAYLOAD_FORMAT
    assert got["tenant"] == "acme"
    assert got["modalities"]["face"][0]["user_id"] == "Kofi Mensah"
    assert got["modalities"]["palm"][0]["embeddings"] == [[0.4, 0.5]]


def test_outer_bundle_shape_is_android_compatible():
    b = bundle.pack(_payload(), "correct horse battery")
    assert b["format"] == bundle.FORMAT
    assert b["kdf"]["algo"] == "PBKDF2-HMAC-SHA256"
    assert b["kdf"]["iterations"] == bundle.PBKDF2_ITERATIONS
    assert b["cipher"]["algo"] == "AES-256-GCM"
    assert b["cipher"]["iv"] and b["cipher"]["ct"]     # base64 fields present


def test_wrong_passphrase_rejected():
    b = bundle.pack(_payload(), "correct horse battery")
    with pytest.raises(bundle.BundleError):
        bundle.unpack(b, "wrong passphrase here")


def test_weak_passphrase_rejected():
    with pytest.raises(bundle.BundleError):
        bundle.pack(_payload(), "short")


def test_tamper_detected():
    b = bundle.pack(_payload(), "correct horse battery")
    b["cipher"]["ct"] = b["cipher"]["ct"][:-4] + "AAAA"    # flip trailing bytes
    with pytest.raises(bundle.BundleError):
        bundle.unpack(b, "correct horse battery")


def test_malformed_bundle_rejected():
    with pytest.raises(bundle.BundleError):
        bundle.unpack({"format": "faceverify-bundle"}, "correct horse battery")
