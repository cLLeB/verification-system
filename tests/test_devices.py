"""Device registry: pairing lifecycle, heartbeat, disable = key revoked."""

from __future__ import annotations

import os
import time

import pytest

from face_service import devices

T = "t_device_test"


@pytest.fixture(autouse=True)
def fresh_devices():
    df = os.environ["FACE_DEVICES_FILE"]
    if os.path.exists(df):
        os.remove(df)
    yield


def _minter(minted: list):
    def mint(name, tenant):
        rec = {"api_key": f"fk_test_{len(minted)}", "key_id": f"k_test_{len(minted)}",
               "signing_secret": "s", "tenant": tenant, "name": name}
        minted.append(rec)
        return rec
    return mint


def test_pairing_end_to_end():
    minted = []
    p = devices.create_pairing(T, "Front gate kiosk", by="admin")
    assert p["pairing_code"].startswith("pc_") and p["device_id"].startswith("dv_")

    out = devices.redeem(p["pairing_code"], _minter(minted))
    assert out is not None
    assert out["device_id"] == p["device_id"]
    assert out["api_key"] == minted[0]["api_key"]     # its OWN fresh verify key
    assert minted[0]["name"] == "Front gate kiosk"

    dev = devices.get(p["device_id"])
    assert dev["tenant"] == T and dev["key_id"] == minted[0]["key_id"]
    assert not dev["disabled"] and dev["last_seen"] is None


def test_pairing_code_is_single_use_and_bogus_codes_fail():
    minted = []
    p = devices.create_pairing(T, "Kiosk")
    assert devices.redeem(p["pairing_code"], _minter(minted)) is not None
    assert devices.redeem(p["pairing_code"], _minter(minted)) is None   # burned
    assert devices.redeem("pc_not_a_real_code", _minter(minted)) is None
    assert devices.redeem("", _minter(minted)) is None
    assert len(minted) == 1                           # no key minted for failures


def test_expired_pairing_code_fails(monkeypatch):
    p = devices.create_pairing(T, "Kiosk")
    real = time.time
    monkeypatch.setattr(time, "time",
                        lambda: real() + devices.PAIRING_TTL_SECONDS + 5)
    assert devices.redeem(p["pairing_code"], _minter([])) is None


def test_heartbeat_updates_last_seen_and_is_tenant_scoped():
    p = devices.create_pairing(T, "Kiosk")
    devices.redeem(p["pairing_code"], _minter([]))
    fresh = devices.heartbeat(p["device_id"], T, info={"app": "1.2.0", "battery": 87})
    assert fresh["last_seen"] is not None
    assert fresh["info"]["app"] == "1.2.0"
    # another tenant can never touch this device's row
    assert devices.heartbeat(p["device_id"], "other_tenant") is None


def test_disable_revokes_the_key_and_blocks_heartbeat():
    revoked = []
    p = devices.create_pairing(T, "Kiosk")
    out = devices.redeem(p["pairing_code"], _minter([]))
    dev = devices.disable(p["device_id"], T, revoked.append)
    assert dev["disabled"] and revoked == [out["key_id"]]
    assert devices.heartbeat(p["device_id"], T) is None
    # wrong tenant cannot disable
    assert devices.disable(p["device_id"], "other_tenant", revoked.append) is None


def test_for_key_resolves_the_device():
    p = devices.create_pairing(T, "Kiosk")
    out = devices.redeem(p["pairing_code"], _minter([]))
    assert devices.for_key(out["key_id"])["device_id"] == p["device_id"]
    assert devices.for_key("k_unknown") is None


def test_rename_and_list():
    p = devices.create_pairing(T, "Old name")
    devices.redeem(p["pairing_code"], _minter([]))
    assert devices.rename(p["device_id"], T, "New name")["name"] == "New name"
    assert devices.rename(p["device_id"], T, "") is None
    assert [d["name"] for d in devices.list_for(T)] == ["New name"]


def test_remove_tenant_revokes_all_device_keys():
    revoked = []
    for n in ("A", "B"):
        p = devices.create_pairing(T, n)
        devices.redeem(p["pairing_code"], _minter([]))
    assert devices.remove_tenant(T, key_revoker=revoked.append) == 2
    assert len(revoked) == 2
    assert devices.list_for(T) == []
