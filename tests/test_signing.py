"""Ed25519 helpers: keygen, key ids, sign/verify (verify never raises)."""
from biometric.core import signing


def test_generate_shapes():
    sk, pk = signing.generate()
    assert len(sk) == 32 and len(pk) == 32 and sk != pk


def test_kid_is_stable_16_hex():
    _, pk = signing.generate()
    k = signing.kid(pk)
    assert len(k) == 16 and int(k, 16) >= 0
    assert signing.kid(pk) == k


def test_sign_verify_round_trip():
    sk, pk = signing.generate()
    sig = signing.sign(sk, b"hello")
    assert len(sig) == 64
    assert signing.verify(pk, b"hello", sig)


def test_verify_rejects_tamper_and_never_raises():
    sk, pk = signing.generate()
    sig = signing.sign(sk, b"hello")
    assert not signing.verify(pk, b"HELLO", sig)
    assert not signing.verify(pk, b"hello", sig[:-1] + bytes([sig[-1] ^ 1]))
    _, other_pk = signing.generate()
    assert not signing.verify(other_pk, b"hello", sig)
    assert not signing.verify(b"short", b"hello", sig)          # malformed key
    assert not signing.verify(pk, b"hello", b"not-a-signature")  # malformed sig
