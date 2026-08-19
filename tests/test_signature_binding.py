"""A signed verdict says "genuine". It should also say "yours, and for this check".

Without binding, a captured verify response stays valid forever and against any
session, so every integrator has to store nonces and reject reuse to get freshness
back. The binding covers the liveness token and request id, chained onto the
original HMAC so it cannot be lifted onto a different verdict - and the original
HMAC is untouched, so verifiers that only know about it keep working.
"""
import hashlib
import hmac
import json

from face_service import keys


def _h(key):
    return {"X-API-Key": key}


def _classic_ok(payload, secret):
    """Signature check as it was before binding existed."""
    sig = payload["signature"]
    body = json.dumps({k: payload.get(k) for k in ("success", "match", "user_id", "score", "best_score")},
                      sort_keys=True, separators=(",", ":"))
    expect = hmac.new(secret.encode(), f"{sig['ts']}.{sig['nonce']}.{body}".encode(),
                      hashlib.sha256).hexdigest()
    return hmac.compare_digest(expect, sig["hmac"])


def _binding_ok(payload, secret, expect_token):
    sig = payload["signature"]
    bound = sig.get("bound") or {}
    if bound.get("token") != expect_token:
        return False
    body = json.dumps(bound, sort_keys=True, separators=(",", ":"))
    expect = hmac.new(secret.encode(),
                      f"{sig['ts']}.{sig['nonce']}.{sig['hmac']}.{body}".encode(),
                      hashlib.sha256).hexdigest()
    return hmac.compare_digest(expect, sig.get("binding", ""))


def _verify(client, key, token, probe_image):
    return client.post("/v1/verify", headers=_h(key),
                       json={"user_id": "sig_subject", "image": probe_image,
                             "token": token}).get_json()


def test_the_verdict_is_bound_to_the_challenge_it_answered(client, probe_image):
    minted = keys.create_key("sig", "t_sig", "verify")
    r = _verify(client, minted["api_key"], "challenge-abc", probe_image)

    assert _classic_ok(r, minted["signing_secret"]), "the original signature must still verify"
    assert _binding_ok(r, minted["signing_secret"], "challenge-abc")


def test_a_verdict_captured_from_another_check_no_longer_passes(client, probe_image):
    """The replay this closes: a genuine response, presented for a different session."""
    minted = keys.create_key("sig_replay", "t_sig", "verify")
    captured = _verify(client, minted["api_key"], "challenge-one", probe_image)

    # still genuine, and still verifies the old way - that is precisely the problem
    assert _classic_ok(captured, minted["signing_secret"])
    # but it did not answer the challenge this check issued
    assert not _binding_ok(captured, minted["signing_secret"], "challenge-two")


def test_the_binding_cannot_be_moved_onto_another_verdict(client, probe_image):
    minted = keys.create_key("sig_swap", "t_sig", "verify")
    first = _verify(client, minted["api_key"], "challenge-one", probe_image)
    second = _verify(client, minted["api_key"], "challenge-two", probe_image)

    forged = dict(second)
    forged["signature"] = {**second["signature"],
                           "bound": first["signature"]["bound"],
                           "binding": first["signature"]["binding"]}
    assert not _binding_ok(forged, minted["signing_secret"], "challenge-one")


def test_the_sdk_checks_both_halves(client, probe_image):
    import sys
    sys.path.insert(0, "sdk/python")
    from faceverify import FaceVerifyClient

    minted = keys.create_key("sig_sdk", "t_sig", "verify")
    fv = FaceVerifyClient(base_url="http://unused", api_key=minted["api_key"],
                    signing_secret=minted["signing_secret"])
    r = _verify(client, minted["api_key"], "challenge-sdk", probe_image)

    assert fv.verify_signature(r) is True                              # unchanged behaviour
    assert fv.verify_signature(r, expect_token="challenge-sdk") is True
    assert fv.verify_signature(r, expect_token="someone-elses") is False
