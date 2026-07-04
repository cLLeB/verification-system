"""Template envelope: versioned CBOR container round-trip + strict validation."""
import pytest

from biometric.core import envelope
from biometric.core.envelope import EnvelopeError


def test_round_trip_minimal():
    blob = envelope.encode(mod="face", kind="raw", data=b"\x01\x02\x03", dim=3, dtype="f32")
    assert envelope.is_envelope(blob)
    env = envelope.decode(blob)
    assert env["v"] == envelope.VERSION
    assert env["mod"] == "face" and env["kind"] == "raw"
    assert env["dim"] == 3 and env["dtype"] == "f32"
    assert env["data"] == b"\x01\x02\x03"
    assert "seedref" not in env and "meta" not in env


def test_round_trip_full():
    blob = envelope.encode(mod="palm", kind="quantized-protected", data=b"\x00" * 16,
                           dim=16, dtype="i8", seedref="cred:abc123",
                           meta={"engine_version": "1.0", "quality": 0.9})
    env = envelope.decode(blob)
    assert env["seedref"] == "cred:abc123"
    assert env["meta"]["quality"] == 0.9


def test_not_an_envelope():
    assert not envelope.is_envelope(b"FT2xxxx")
    assert not envelope.is_envelope(b"")
    assert not envelope.is_envelope(None)
    with pytest.raises(EnvelopeError):
        envelope.decode(b"FT2xxxx")


def test_rejects_garbage_cbor():
    with pytest.raises(EnvelopeError):
        envelope.decode(envelope.MAGIC + b"\xff\xff\xff")


def test_rejects_bad_fields():
    good = dict(mod="face", kind="raw", data=b"x", dim=1, dtype="f32")
    for bad in (dict(good, mod="iris"), dict(good, kind="plain"),
                dict(good, dtype="f64"), dict(good, dim=-1), dict(good, data=b"")):
        with pytest.raises(EnvelopeError):
            envelope.encode(**bad)


def test_rejects_unknown_field_and_wrong_version():
    import cbor2
    env = {"v": 99, "mod": "face", "kind": "raw", "dim": 1, "dtype": "f32", "data": b"x"}
    with pytest.raises(EnvelopeError):
        envelope.decode(envelope.MAGIC + cbor2.dumps(env))
    env = {"v": 1, "mod": "face", "kind": "raw", "dim": 1, "dtype": "f32",
           "data": b"x", "surprise": True}
    with pytest.raises(EnvelopeError):
        envelope.decode(envelope.MAGIC + cbor2.dumps(env))
