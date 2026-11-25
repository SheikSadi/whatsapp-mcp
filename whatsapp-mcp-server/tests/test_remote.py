"""Tests for the remote (Streamable HTTP) entry point: auth, /setup/qr, /healthz."""

import base64

import pytest
from starlette.testclient import TestClient

import remote

TOKEN = "t" * 40
BASE = "/whatsapp"


class FakeBridge:
    def __init__(self, status, after_reconnect=None):
        self.status = status
        self.after_reconnect = after_reconnect or status
        self.reconnects = 0

    async def get(self, client):
        return self.status

    async def reconnect(self, client):
        self.reconnects += 1
        self.status = self.after_reconnect
        return self.status


@pytest.fixture
def make_client(monkeypatch):
    def _make(bridge: FakeBridge | None = None):
        if bridge is not None:
            monkeypatch.setattr(remote, "_bridge_get_connection", bridge.get)
            monkeypatch.setattr(remote, "_bridge_reconnect", bridge.reconnect)
        # build_app registers routes on the shared FastMCP instance; reset them per test.
        monkeypatch.setattr(remote.mcp, "_custom_starlette_routes", [])
        monkeypatch.setattr(remote.mcp, "_session_manager", None)
        app = remote.build_app(TOKEN, "mcp.example.test", BASE, 8791)
        return TestClient(app, base_url="http://127.0.0.1:8791")

    return _make


def bearer(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def basic(password=TOKEN, user="me"):
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def test_healthz_is_public_and_says_nothing_else(make_client):
    r = make_client().get(f"{BASE}/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, bearer("wrong" * 10), basic(), {"Authorization": TOKEN}])
def test_mcp_requires_bearer(make_client, headers):
    r = make_client().post(f"{BASE}/mcp", headers=headers, json={})
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")


def test_mcp_accepts_bearer(make_client):
    with make_client() as client:
        r = client.post(
            f"{BASE}/mcp",
            headers={**bearer(), "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
        )
    assert r.status_code == 200, r.text
    assert "whatsapp" in r.text


def test_mcp_rejects_foreign_host(make_client):
    with make_client() as client:
        r = client.post(f"{BASE}/mcp", headers={**bearer(), "Host": "evil.test"}, json={})
    assert r.status_code == 421


def test_setup_qr_requires_auth(make_client):
    bridge = FakeBridge({"paired": True, "state": "connected"})
    r = make_client(bridge).get(f"{BASE}/setup/qr")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")
    assert make_client(bridge).get(f"{BASE}/setup/qr", headers=basic("nope" * 10)).status_code == 401


def test_setup_qr_token_in_query_is_not_accepted(make_client):
    bridge = FakeBridge({"paired": True, "state": "connected"})
    assert make_client(bridge).get(f"{BASE}/setup/qr?token={TOKEN}").status_code == 401


def test_setup_qr_paired_is_409(make_client):
    bridge = FakeBridge({"paired": True, "state": "connected"})
    client = make_client(bridge)
    r = client.get(f"{BASE}/setup/qr", headers=bearer())
    assert r.status_code == 409
    assert "Already paired" in r.text
    assert client.get(f"{BASE}/setup/qr?format=png", headers=basic()).status_code == 409


def test_setup_qr_serves_current_code(make_client):
    bridge = FakeBridge({"paired": False, "state": "pairing", "qrCode": "2@first"})
    client = make_client(bridge)
    r = client.get(f"{BASE}/setup/qr", headers=basic())
    assert r.status_code == 200
    assert "data:image/png;base64," in r.text
    assert 'http-equiv="refresh"' in r.text
    assert r.headers["cache-control"] == "no-store"
    png1 = client.get(f"{BASE}/setup/qr?format=png", headers=bearer()).content
    assert png1.startswith(b"\x89PNG")
    bridge.status = {"paired": False, "state": "pairing", "qrCode": "2@rotated"}
    png2 = client.get(f"{BASE}/setup/qr?format=png", headers=bearer()).content
    assert png1 != png2  # never a cached first code
    assert bridge.reconnects == 0


def test_setup_qr_restarts_linking_when_no_code(make_client):
    bridge = FakeBridge(
        {"paired": False, "state": "logged_out", "qrCode": None},
        after_reconnect={"paired": False, "state": "connecting", "qrCode": None},
    )
    r = make_client(bridge).get(f"{BASE}/setup/qr", headers=bearer())
    assert bridge.reconnects == 1
    assert r.status_code == 503
    assert "refresh" in r.text


def test_setup_qr_bridge_down(make_client, monkeypatch):
    import httpx

    async def boom(client):
        raise httpx.ConnectError("down")

    client = make_client()
    monkeypatch.setattr(remote, "_bridge_get_connection", boom)
    r = client.get(f"{BASE}/setup/qr", headers=bearer())
    assert r.status_code == 503


def test_short_token_refused():
    with pytest.raises(ValueError):
        remote.BearerAuthMiddleware(lambda *a: None, token="short", public_paths=set(), basic_paths=set())


def test_check_authorization_variants():
    assert remote.check_authorization(f"Bearer {TOKEN}", TOKEN, allow_basic=False)
    assert remote.check_authorization(f"bearer   {TOKEN}", TOKEN, allow_basic=False)
    assert not remote.check_authorization(basic()["Authorization"], TOKEN, allow_basic=False)
    assert remote.check_authorization(basic()["Authorization"], TOKEN, allow_basic=True)
    assert not remote.check_authorization("Basic !!!", TOKEN, allow_basic=True)
    assert not remote.check_authorization(None, TOKEN, allow_basic=True)
