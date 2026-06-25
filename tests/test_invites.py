"""Enrolment-invite store: pre-assigned identity, single-use burn-on-finish,
short expiry, revoke, tenant isolation, and txt-roster parsing.

An invite is a cryptographically-random token (stored HASHED) that authorises ONE
unsupervised onboarding session for ONE pre-assigned ``user_id`` within one tenant.
"""
import time


def test_create_and_lookup(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("Kofi Mensah", "first_party")
    assert info["token"].startswith("inv_") and info["invite_id"].startswith("inv_") is False
    assert info["invite_id"].startswith("iv_")
    rec = inv.lookup(info["token"])
    assert rec is not None
    assert rec["user_id"] == "Kofi Mensah"          # spaces preserved verbatim
    assert rec["tenant"] == "first_party"
    assert inv.lookup("inv_nonexistent") is None


def test_pre_assigned_name_trims_ends_only(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("  Kofi Mensah  ", "first_party")
    rec = inv.lookup(info["token"])
    assert rec["user_id"] == "Kofi Mensah"          # ends trimmed, inner space kept


def test_consume_burns_the_token(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("Ama", "first_party")
    assert inv.lookup(info["token"]) is not None
    assert inv.consume(info["token"]) is True
    assert inv.lookup(info["token"]) is None        # used -> no longer valid
    assert inv.state(info["token"]) == "used"
    assert inv.consume(info["token"]) is False       # can't burn twice


def test_expiry(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("Yaw", "first_party", expires_in_hours=24)
    assert inv.lookup(info["token"]) is not None
    data = inv._load()
    for h, v in data.items():
        if v["invite_id"] == info["invite_id"]:
            v["expires"] = int(time.time()) - 10     # force already-expired
    inv._save(data)
    assert inv.lookup(info["token"]) is None
    assert inv.state(info["token"]) == "expired"


def test_revoke(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("Esi", "first_party")
    assert inv.revoke(info["invite_id"]) is True
    assert inv.lookup(info["token"]) is None
    assert inv.state(info["token"]) == "revoked"


def test_bulk_create_and_progress(fresh_invites):
    inv = fresh_invites
    batch = inv.create_invites(["Kofi Mensah", "Ama", "Yaw"], "first_party")
    assert len(batch) == 3
    tokens = {b["token"] for b in batch}
    assert len(tokens) == 3                          # all distinct
    # progress is tracked but does NOT burn the token (resume after refresh)
    t0 = batch[0]["token"]
    inv.mark_progress(t0, "face")
    assert inv.lookup(t0) is not None
    assert "face" in inv.lookup(t0)["enrolled"]


def test_list_is_tenant_scoped_and_hides_token(fresh_invites):
    inv = fresh_invites
    inv.create_invites(["A", "B"], "acme")
    inv.create_invite("C", "globex")
    acme = inv.list_invites("acme")
    assert len(acme) == 2
    assert all(i["tenant"] == "acme" for i in acme)
    assert all("token" not in i for i in acme)       # raw token never re-exposed
    assert {i["user_id"] for i in acme} == {"A", "B"}
    assert len(inv.list_invites("globex")) == 1
    assert len(inv.list_invites()) == 3              # no filter -> all


def test_revoke_for_tenant(fresh_invites):
    inv = fresh_invites
    inv.create_invites(["A", "B"], "acme")
    inv.create_invite("C", "globex")
    assert inv.revoke_for_tenant("acme") == 2
    assert len(inv.list_invites("acme")) == 0
    assert len(inv.list_invites("globex")) == 1


def test_parse_roster_lines_and_commas(fresh_invites):
    inv = fresh_invites
    text = "Kofi Mensah\nAma Owusu, Yaw Boateng\n\n  Kofi Mensah  \n"
    names = inv.parse_roster(text)
    # split on newline AND comma, trim ends, drop blanks, dedupe preserving order
    assert names == ["Kofi Mensah", "Ama Owusu", "Yaw Boateng"]
