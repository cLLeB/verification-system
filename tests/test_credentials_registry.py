"""Credential registry + revocation: record/list/revoke, per-user auto-revoke,
exact vs bloom revocation lists (no false negatives), offboard removal."""
import pytest

from face_service import credentials


@pytest.fixture(autouse=True)
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BIO_CREDENTIALS_DIR", str(tmp_path / "creds"))


def _cid(i):
    return f"{i:032x}"


def test_record_list_revoke():
    credentials.record("acme", _cid(1), "alice", ["face"], 100, 200, name="Alice")
    credentials.record("acme", _cid(2), "bob", ["face", "palm"], 150, 250)
    assert len(credentials.list_for("acme")) == 2
    assert credentials.list_for("acme", "alice")[0]["name"] == "Alice"
    assert credentials.list_for("other") == []
    assert credentials.revoke("acme", _cid(1)) is True
    assert credentials.revoke("acme", _cid(1)) is False      # already revoked
    assert credentials.revoke("acme", _cid(9)) is False      # unknown
    assert credentials.is_revoked("acme", _cid(1))
    assert not credentials.is_revoked("acme", _cid(2))
    assert credentials.revoked_cids("acme") == [_cid(1)]


def test_revoke_for_user_and_offboard():
    for i in range(3):
        credentials.record("acme", _cid(10 + i), "alice", ["face"], 100, 200)
    credentials.record("acme", _cid(20), "bob", ["face"], 100, 200)
    assert credentials.revoke_for_user("acme", "alice") == 3
    assert credentials.revoke_for_user("acme", "alice") == 0   # idempotent
    assert not credentials.is_revoked("acme", _cid(20))
    assert credentials.remove_tenant("acme") is True
    assert credentials.remove_tenant("acme") is False
    assert credentials.list_for("acme") == []


def test_exact_revocation_list():
    credentials.record("t", _cid(1), "u", ["face"], 1, 2)
    credentials.revoke("t", _cid(1))
    rev = credentials.build_revocation_list("t")
    assert rev["count"] == 1 and rev["exact"] == [_cid(1)]
    assert credentials.check_revoked(rev, _cid(1))
    assert not credentials.check_revoked(rev, _cid(2))
    assert not credentials.check_revoked({}, _cid(1))


def test_bloom_revocation_no_false_negatives():
    revoked = [_cid(i) for i in range(150)]                 # > EXACT_LIMIT
    for c in revoked:
        credentials.record("t", c, "u", ["face"], 1, 2)
        credentials.revoke("t", c)
    rev = credentials.build_revocation_list("t")
    assert "bloom" in rev and rev["count"] == 150
    assert all(credentials.check_revoked(rev, c) for c in revoked)   # NEVER a false negative
    clean = [_cid(10_000 + i) for i in range(2000)]
    fp = sum(credentials.check_revoked(rev, c) for c in clean)
    assert fp / len(clean) < 0.02                            # near the target FPR
    assert credentials.check_revoked({"bloom": {"m": "junk"}}, _cid(1))  # malformed: fail closed
