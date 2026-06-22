"""Shared pytest fixtures + isolated test state.

Env vars are set BEFORE any face_service module is imported so the key/audit/usage
stores point at throwaway files and the admin password is deterministic. API tests
import the Flask app once (which warms the ONNX models — a few seconds, then cached).
"""

from __future__ import annotations

import base64
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
_TMP = os.path.join(_ROOT, "tests", "_test_state")   # matches .gitignore tests/_test_*
# Start every session from a clean slate so usage/quota/idempotency counters from a
# previous run can't leak into tests that assert exact totals.
import shutil
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)

os.environ["FACE_KEYS_FILE"] = os.path.join(_TMP, "apikeys.json")
os.environ["FACE_AUDIT_DIR"] = os.path.join(_TMP, "audit")
os.environ["FACE_USAGE_FILE"] = os.path.join(_TMP, "usage.json")
os.environ["FACE_DB_PATH"] = os.path.join(_TMP, "face_db")   # isolated store/tenants
os.environ["FACE_ADMINS_FILE"] = os.path.join(_TMP, "admins.json")
os.environ["FACE_TENANTS_FILE"] = os.path.join(_TMP, "tenants.json")
os.environ["FACE_ADMIN_PASSWORD"] = "test-pw"
os.environ["FACE_SECRET_KEY"] = "test-secret"
os.environ["FACE_RATE_LIMIT"] = "100000"          # don't let rate limiting trip tests
os.environ.setdefault("FACE_DEBUG", "0")

_DEBUG = os.path.join(_ROOT, "debug")


def _b64(path: str) -> str:
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def _debug_images(prefix: str):
    return sorted(_b64(os.path.join(_DEBUG, n)) for n in os.listdir(_DEBUG)
                  if n.startswith(prefix) and n.endswith(".jpg"))


@pytest.fixture(scope="session")
def enroll_images():
    imgs = _debug_images("enroll_")
    if not imgs:
        pytest.skip("no debug enrol images available")
    return imgs


@pytest.fixture(scope="session")
def probe_image():
    imgs = _debug_images("verify_")
    if not imgs:
        pytest.skip("no debug verify images available")
    return imgs[0]


@pytest.fixture(scope="session")
def app_module():
    """Import the Flask app once (warms models). Skips if models are unavailable
    (e.g. CI without the InsightFace pack) so the unit tests still run."""
    try:
        import app
    except Exception as exc:                      # pragma: no cover
        pytest.skip(f"face models unavailable: {exc}")
    if not getattr(app, "MODEL_READY", False):
        pytest.skip("face models not ready (no model pack)")
    return app


@pytest.fixture
def client(app_module):
    return app_module.app.test_client()


@pytest.fixture
def fresh_keys():
    """Empty the key store before a test that asserts on key contents."""
    kf = os.environ["FACE_KEYS_FILE"]
    if os.path.exists(kf):
        os.remove(kf)
    from face_service import keys
    return keys


@pytest.fixture
def make_key():
    from face_service import keys

    def _make(role="admin", tenant=None, name="test"):
        return keys.create_key(name, tenant or f"t_{role}", role)["api_key"]
    return _make
