"""Python SDK issuer-key methods hit the right endpoints with the right bodies."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))
from faceverify import FaceVerifyClient  # noqa: E402


def test_sdk_methods_call_expected_paths(monkeypatch):
    calls = []
    client = FaceVerifyClient("https://example.test", "fk_x")

    def fake_call(method, path, body=None):
        calls.append((method, path, body))
        return {"success": True}

    monkeypatch.setattr(client, "_call", fake_call)
    client.tenant_keys()
    client.rotate_tenant_keys()
    assert calls[0] == ("GET", "/v1/tenant/keys", None)
    assert calls[1] == ("POST", "/v1/tenant/keys/rotate", {"confirm": True})
