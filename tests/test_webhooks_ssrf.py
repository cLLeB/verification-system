"""Webhook SSRF guard: internal/metadata targets are refused."""
from face_service import webhooks as wh


def test_blocks_loopback_and_metadata_and_private():
    assert not wh.is_safe_url("http://127.0.0.1/hook")
    assert not wh.is_safe_url("http://169.254.169.254/latest/meta-data")  # cloud metadata
    assert not wh.is_safe_url("http://10.0.0.5/hook")
    assert not wh.is_safe_url("http://192.168.1.10:8080/hook")
    assert not wh.is_safe_url("http://[::1]/hook")


def test_blocks_bad_scheme():
    assert not wh.is_safe_url("file:///etc/passwd")
    assert not wh.is_safe_url("ftp://example.com/x")
    assert not wh.is_safe_url("not a url")


def test_allows_public_ip_literal():
    assert wh.is_safe_url("https://8.8.8.8/hook")   # public IP, no DNS needed
