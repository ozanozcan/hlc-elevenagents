"""Tunnel guard (d#1113): the tunnel host may only reach the voice tool surface.

The dev backend runs stub auth, so without this an ngrok URL would hand a team
admin session to the internet.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.auth.dependencies import AuthContext, get_auth_context
from app.config import settings
from app.db.session import engine as app_engine
from app.main import app
from app.middleware.tunnel_guard import TunnelGuardMiddleware

_TUNNEL = "demo.ngrok-free.app"


def _mini_app() -> FastAPI:
    mini = FastAPI()

    @mini.get("/api/v1/plays/saved")
    def saved() -> dict[str, str]:
        return {"route": "saved"}

    @mini.get("/voice-tools/plays")
    def tools() -> dict[str, str]:
        return {"route": "tools"}

    @mini.post("/webhooks/elevenlabs")
    def hook() -> dict[str, str]:
        return {"route": "hook"}

    @mini.post("/webhooks/workos")
    def workos() -> dict[str, str]:
        return {"route": "workos"}

    mini.add_middleware(TunnelGuardMiddleware)
    return mini


@pytest.fixture()
def tunnel_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_tunnel_host", _TUNNEL)


@pytest.fixture()
def mini(tunnel_on: None) -> TestClient:
    return TestClient(_mini_app())


@pytest.mark.parametrize(
    "host", [_TUNNEL, "DEMO.ngrok-free.app", f"{_TUNNEL}:443"]
)
def test_tunnel_host_cannot_reach_product_api(mini: TestClient, host: str) -> None:
    response = mini.get("/api/v1/plays/saved", headers={"host": host})

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "route"),
    [("get", "/voice-tools/plays", "tools"), ("post", "/webhooks/elevenlabs", "hook")],
)
def test_tunnel_host_reaches_voice_surface(
    mini: TestClient, method: str, path: str, route: str
) -> None:
    response = getattr(mini, method)(path, headers={"host": _TUNNEL})

    assert response.status_code == 200
    assert response.json() == {"route": route}


def test_tunnel_host_cannot_reach_other_webhooks(mini: TestClient) -> None:
    assert mini.post("/webhooks/workos", headers={"host": _TUNNEL}).status_code == 404


def test_forwarded_host_rewrite_is_still_guarded(mini: TestClient) -> None:
    """A tunnel that rewrites Host to localhost keeps the original in X-Forwarded-Host."""
    response = mini.get(
        "/api/v1/plays/saved", headers={"host": "localhost:8000", "x-forwarded-host": _TUNNEL}
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "host",
    ["localhost:8000", "127.0.0.1:8000", "[::1]:8000", "testserver", "LOCALHOST", "localhost."],
)
def test_local_host_is_unaffected(mini: TestClient, host: str) -> None:
    response = mini.get("/api/v1/plays/saved", headers={"host": host})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "other.ngrok-free.app",  # tunnel restarted on a different / rotated domain
        f"{_TUNNEL}.",  # FQDN trailing dot
        "evil.example.com:8000",
        "localhost.evil.example.com",
        "8.8.8.8:8000",  # a public IP is not local
        "100.64.1.2",  # CGNAT is not private
    ],
)
def test_any_non_local_host_cannot_reach_product_api(mini: TestClient, host: str) -> None:
    """Fail closed (d#1113): not matching the configured host is not enough to pass."""
    response = mini.get("/api/v1/plays/saved", headers={"host": host})

    assert response.status_code == 404


def test_public_forwarded_host_behind_local_host_is_guarded(mini: TestClient) -> None:
    response = mini.get(
        "/api/v1/plays/saved",
        headers={"host": "localhost", "x-forwarded-host": "other.ngrok-free.app"},
    )

    assert response.status_code == 404


def test_other_public_host_still_reaches_voice_surface(mini: TestClient) -> None:
    response = mini.get("/voice-tools/plays", headers={"host": "other.ngrok-free.app"})

    assert response.status_code == 200
    assert response.json() == {"route": "tools"}


def test_unset_tunnel_host_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voice_tunnel_host", "")
    response = TestClient(_mini_app()).get("/api/v1/plays/saved", headers={"host": _TUNNEL})

    assert response.status_code == 200


@pytest.fixture()
def real_client(tunnel_on: None) -> Iterator[TestClient]:
    def _deny() -> AuthContext:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Deny auth so a request that gets past the guard stops at 401, not at the DB.
    app.dependency_overrides[get_auth_context] = _deny
    try:
        with TestClient(app) as test_client:
            yield test_client
            test_client.portal.call(app_engine.dispose)
    finally:
        app.dependency_overrides.pop(get_auth_context, None)


def test_real_app_guards_product_api_on_tunnel_host(real_client: TestClient) -> None:
    response = real_client.get("/api/v1/plays/saved", headers={"host": _TUNNEL})

    assert response.status_code == 404


def test_real_app_lets_tunnel_reach_voice_tools(real_client: TestClient) -> None:
    response = real_client.get(
        "/voice-tools/plays", params={"q": "smash"}, headers={"host": _TUNNEL}
    )

    assert response.status_code == 401


def test_real_app_local_host_reaches_product_api(real_client: TestClient) -> None:
    response = real_client.get("/api/v1/plays/saved", headers={"host": "localhost:8000"})

    assert response.status_code == 401


@pytest.mark.parametrize(
    "forwarded",
    ["192.168.1.20:3000", "10.0.0.5:3000", "172.16.4.4", "[fe80::1]:3000", "ozans-mbp.local:3000"],
)
def test_lan_dev_through_next_proxy_is_unaffected(mini: TestClient, forwarded: str) -> None:
    """A phone on the LAN goes through Next's rewrite: Host localhost, X-Forwarded-Host the LAN host."""
    response = mini.get(
        "/api/v1/plays/saved", headers={"host": "localhost:8000", "x-forwarded-host": forwarded}
    )

    assert response.status_code == 200
