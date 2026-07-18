"""PII scrub: email/phone/card/IP detection, redaction, counts."""

from __future__ import annotations

from face_service import piiscrub
from face_service import checkdigit


def test_email_redacted():
    out = piiscrub.scrub("contact me at ama@example.com please")
    assert "[EMAIL]" in out["text"] and "ama@example.com" not in out["text"]
    assert out["redactions"]["EMAIL"] == 1


def test_ipv4_redacted():
    out = piiscrub.scrub("from 192.168.1.42 today")
    assert "[IPV4]" in out["text"]


def test_valid_card_redacted():
    card = checkdigit.luhn_generate("453201511283003")   # 16-digit valid Luhn
    out = piiscrub.scrub(f"card {card} on file")
    assert "[CARD]" in out["text"]


def test_invalid_card_not_redacted_as_card():
    # a random 16-digit run that fails Luhn should not be a CARD
    text = "ref 1234567890123456 here"
    found_types = {m["type"] for m in piiscrub.detect(text)}
    assert "CARD" not in found_types


def test_phone_redacted():
    out = piiscrub.scrub("call +233 24 123 4567 now")
    assert "[PHONE]" in out["text"]


def test_multiple_and_counts():
    text = "ama@x.com and kofi@y.com from 10.0.0.1"
    out = piiscrub.scrub(text)
    assert out["redactions"]["EMAIL"] == 2 and out["redactions"]["IPV4"] == 1
    assert out["total"] == 3


def test_no_pii():
    assert not piiscrub.has_pii("just a normal sentence with no secrets")
    out = piiscrub.scrub("nothing here")
    assert out["text"] == "nothing here" and out["total"] == 0


def test_custom_mask():
    out = piiscrub.scrub("mail ama@x.com", mask={"EMAIL": "***"})
    assert out["text"] == "mail ***"


def test_detect_spans():
    matches = piiscrub.detect("x ama@x.com y")
    assert matches[0]["type"] == "EMAIL" and matches[0]["value"] == "ama@x.com"
