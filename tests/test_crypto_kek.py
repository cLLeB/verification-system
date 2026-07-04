"""KEK-wrapped store data keys: new stores, legacy migration, master rotation,
crypto-erase."""
import os

import pytest

from biometric.core import crypto


def _files(d):
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_fresh_store_with_passphrase_gets_wrapped_key(tmp_path):
    d = str(tmp_path / "s1")
    c = crypto.get_cipher(d, passphrase="master-pw")
    token = c.encrypt(b"secret template")
    assert ".key.wrapped" in _files(d) and ".salt" in _files(d)
    assert ".key" not in _files(d)
    # reopening yields the same data key
    c2 = crypto.get_cipher(d, passphrase="master-pw")
    assert c2.decrypt(token) == b"secret template"


def test_wrong_passphrase_fails_fast_on_wrapped_store(tmp_path):
    d = str(tmp_path / "s2")
    crypto.get_cipher(d, passphrase="right")
    with pytest.raises(Exception):
        crypto.get_cipher(d, passphrase="wrong")


def test_plaintext_keyfile_store_migrates_to_wrapped(tmp_path):
    d = str(tmp_path / "s3")
    c = crypto.get_cipher(d, passphrase="")       # no passphrase -> plaintext .key
    token = c.encrypt(b"old data")
    assert ".key" in _files(d)
    c2 = crypto.get_cipher(d, passphrase="master-pw")   # passphrase introduced
    assert c2.decrypt(token) == b"old data"       # same data key, now wrapped
    assert ".key.wrapped" in _files(d) and ".key" not in _files(d)


def test_legacy_derived_store_still_reads(tmp_path):
    """Stores encrypted by the OLD code path (salt + passphrase-derived key,
    no key files) must keep decrypting unchanged."""
    d = str(tmp_path / "s4")
    crypto.get_cipher(d, passphrase="pw")         # fresh -> wrapped in new code
    # simulate a legacy store: remove the wrapped key, keep only the salt;
    # legacy data was encrypted with the KDF-derived key directly
    os.remove(os.path.join(d, ".key.wrapped"))
    legacy_cipher = crypto.get_cipher(d, passphrase="pw")
    token = legacy_cipher.encrypt(b"legacy blob")
    assert crypto.get_cipher(d, passphrase="pw").decrypt(token) == b"legacy blob"


def test_rotate_master_rewraps_without_reencrypting(tmp_path):
    d = str(tmp_path / "s5")
    token = crypto.get_cipher(d, passphrase="old-pw").encrypt(b"data")
    assert crypto.rotate_master(d, "old-pw", "new-pw") is True
    assert crypto.get_cipher(d, passphrase="new-pw").decrypt(token) == b"data"
    with pytest.raises(Exception):
        crypto.get_cipher(d, passphrase="old-pw")


def test_rotate_master_refuses_legacy_and_keyfile_stores(tmp_path):
    d = str(tmp_path / "s6")
    crypto.get_cipher(d, passphrase="")                    # key-file store
    assert crypto.rotate_master(d, "a", "b") is False


def test_erase_keys_makes_data_unrecoverable(tmp_path):
    d = str(tmp_path / "s7")
    crypto.get_cipher(d, passphrase="pw").encrypt(b"data")
    removed = crypto.erase_keys(d)
    assert removed >= 2                                    # .salt + .key.wrapped
    assert not any(f in _files(d) for f in (".salt", ".key", ".key.wrapped"))
