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


# --- modality scoping + step-up (fix A: second-modality invite hijack) --------

def test_default_invite_allows_both_modalities(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("Kofi", "first_party")
    assert info["modalities"] == ["face", "palm"]
    assert info["requires_step_up"] is False
    rec = inv.lookup(info["token"])
    assert inv.allowed_modalities(rec) == ["face", "palm"]
    assert inv.is_step_up_satisfied(rec) is True     # no step-up needed


def test_modality_scoped_invite(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("Kofi", "first_party", modalities=["palm"])
    assert info["modalities"] == ["palm"]
    rec = inv.lookup(info["token"])
    assert inv.allowed_modalities(rec) == ["palm"]


def test_modalities_normalise_to_ordered_valid_subset(fresh_invites):
    inv = fresh_invites
    # unknown entries dropped; order follows MODALITIES; empty -> both
    assert inv.create_invite("A", "t", modalities=["palm", "bogus"])["modalities"] == ["palm"]
    assert inv.create_invite("B", "t", modalities=["palm", "face"])["modalities"] == ["face", "palm"]
    assert inv.create_invite("C", "t", modalities=[])["modalities"] == ["face", "palm"]


def test_mark_progress_rejects_off_whitelist_modality(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("Kofi", "first_party", modalities=["palm"])
    assert inv.mark_progress(info["token"], "face") is False   # face not allowed
    assert inv.mark_progress(info["token"], "palm") is True
    assert inv.lookup(info["token"])["enrolled"] == ["palm"]


def test_step_up_lifecycle(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("Kofi", "first_party", modalities=["palm"],
                             requires_step_up=True, step_up_modality="face")
    assert info["requires_step_up"] is True
    assert info["step_up_modality"] == "face"
    rec = inv.lookup(info["token"])
    assert inv.is_step_up_satisfied(rec) is False              # not proven yet
    assert inv.mark_stepped_up(info["token"]) is True
    assert inv.is_step_up_satisfied(inv.lookup(info["token"])) is True


def test_step_up_modality_must_be_valid(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("Kofi", "first_party", requires_step_up=True,
                             step_up_modality="bogus")
    assert info["step_up_modality"] is None


def test_get_by_invite_id_for_purge(fresh_invites):
    inv = fresh_invites
    info = inv.create_invite("Kofi Mensah", "acme", modalities=["palm"])
    inv.mark_progress(info["token"], "palm")
    rec = inv.get_by_invite_id(info["invite_id"])
    assert rec is not None
    assert rec["user_id"] == "Kofi Mensah"
    assert rec["tenant"] == "acme"
    assert rec["enrolled"] == ["palm"]                         # what to purge on revoke
    assert inv.get_by_invite_id("iv_missing") is None


def test_list_view_exposes_scope_not_token(fresh_invites):
    inv = fresh_invites
    inv.create_invite("Kofi", "acme", modalities=["palm"], requires_step_up=True,
                      step_up_modality="face")
    view = inv.list_invites("acme")[0]
    assert view["modalities"] == ["palm"]
    assert view["requires_step_up"] is True
    assert "token" not in view and "stepped_up" not in view


def test_legacy_record_without_modalities_defaults_both(fresh_invites):
    inv = fresh_invites
    # simulate a record minted before scoping existed (no modalities key)
    info = inv.create_invite("Kofi", "first_party")
    data = inv._load()
    for v in data.values():
        v.pop("modalities", None)
        v.pop("requires_step_up", None)
    inv._save(data)
    rec = inv.lookup(info["token"])
    assert inv.allowed_modalities(rec) == ["face", "palm"]
    assert inv.is_step_up_satisfied(rec) is True
