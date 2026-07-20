from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from mcp.server.auth.provider import AuthorizationParams, RegistrationError
from mcp.shared.auth import OAuthClientInformationFull
from starlette.testclient import TestClient

from driveops_mcp.oauth import OAuthStore, SingleOwnerOAuthProvider
from driveops_mcp.server import build_server, create_http_app
from driveops_mcp.auth import READONLY_SCOPES


def _configured_google_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token = tmp_path / "token.json"
    token.write_text(
        json.dumps(
            {
                "token": "expired-access-token",
                "refresh_token": "refresh-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "scopes": READONLY_SCOPES,
                "expiry": "2020-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))


def test_public_http_fails_closed_without_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configured_google_token(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="Refusing to expose unauthenticated"):
        create_http_app(host="0.0.0.0", public_url="https://mcp.example.com")


def test_local_streaming_http_response_completes() -> None:
    app = create_http_app(host="127.0.0.1")
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/mcp",
            json=initialize,
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 200
    assert "event: message" in response.text


def test_token_http_requires_bearer_and_rejects_wrong_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configured_google_token(monkeypatch, tmp_path)
    token = "generated-test-token-with-at-least-32-bytes"
    monkeypatch.setenv("DRIVEOPS_MCP_AUTH_TOKEN", token)
    app = create_http_app(
        host="0.0.0.0",
        auth_mode="token",
        public_url="https://mcp.example.com",
    )
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }

    with TestClient(app, base_url="https://mcp.example.com") as client:
        assert client.get("/healthz").status_code == 200
        unauthenticated = client.post("/mcp", json=initialize)
        assert unauthenticated.status_code == 401
        authenticated = client.post(
            "/mcp", json=initialize, headers={"Authorization": f"Bearer {token}"}
        )
        assert authenticated.status_code == 200
        wrong_host = client.post(
            "/mcp",
            json=initialize,
            headers={"Authorization": f"Bearer {token}", "Host": "evil.example"},
        )
        assert wrong_host.status_code == 421


def test_public_oauth_http_flow_reaches_authenticated_mcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configured_google_token(monkeypatch, tmp_path)
    callback = "https://client.example/callback"
    access_key = "owner-access-key-with-at-least-32-bytes"
    monkeypatch.setenv("DRIVEOPS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DRIVEOPS_OAUTH_ACCESS_KEY", access_key)
    monkeypatch.setenv("DRIVEOPS_OAUTH_ALLOWED_REDIRECT_URIS", callback)
    app = create_http_app(
        host="0.0.0.0",
        auth_mode="oauth",
        public_url="https://mcp.example.com",
    )
    verifier = "integration-test-code-verifier"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )

    with TestClient(app, base_url="https://mcp.example.com") as client:
        assert client.get("/.well-known/oauth-authorization-server").status_code == 200
        protected = client.get("/.well-known/oauth-protected-resource/mcp")
        assert protected.status_code == 200
        registered = client.post(
            "/register",
            json={
                "redirect_uris": [callback],
                "token_endpoint_auth_method": "client_secret_post",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "driveops",
                "client_name": "Integration client",
            },
        )
        assert registered.status_code == 201
        client_info = registered.json()
        authorize = client.get(
            "/authorize",
            params={
                "client_id": client_info["client_id"],
                "redirect_uri": callback,
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "driveops",
                "state": "state-1",
                "resource": "https://mcp.example.com/mcp",
            },
            follow_redirects=False,
        )
        assert authorize.status_code == 302
        consent = client.get(authorize.headers["location"])
        assert consent.status_code == 200
        request_id = parse_qs(urlsplit(authorize.headers["location"]).query)["request"][
            0
        ]
        approved = client.post(
            "/oauth/authorize",
            data={"request": request_id, "access_key": access_key},
            follow_redirects=False,
        )
        assert approved.status_code == 303
        code = parse_qs(urlsplit(approved.headers["location"]).query)["code"][0]
        token_response = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_info["client_id"],
                "client_secret": client_info["client_secret"],
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": callback,
                "resource": "https://mcp.example.com/mcp",
            },
        )
        assert token_response.status_code == 200
        access_token = token_response.json()["access_token"]
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        initialized = client.post(
            "/mcp",
            json=initialize,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert initialized.status_code == 200


@pytest.mark.asyncio
async def test_single_owner_oauth_flow_rotates_and_hashes_tokens(
    tmp_path: Path,
) -> None:
    callback = "https://client.example/callback"
    store = OAuthStore(tmp_path / "oauth.db")
    provider = SingleOwnerOAuthProvider(
        issuer_url="https://mcp.example.com",
        access_key="owner-access-key-with-at-least-32-bytes",
        allowed_redirect_uris={callback},
        store=store,
    )
    client = OAuthClientInformationFull(
        client_id="client-1",
        client_secret="secret",
        redirect_uris=[callback],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope="driveops",
        client_name="Test client",
    )
    await provider.register_client(client)
    verifier = "test-code-verifier"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    authorize_url = await provider.authorize(
        client,
        AuthorizationParams(
            state="state-1",
            scopes=["driveops"],
            code_challenge=challenge,
            redirect_uri=callback,
            redirect_uri_provided_explicitly=True,
            resource="https://mcp.example.com/mcp",
        ),
    )
    request_id = parse_qs(urlsplit(authorize_url).query)["request"][0]

    assert provider.approve(request_id, "wrong-key") is None
    redirect = provider.approve(request_id, "owner-access-key-with-at-least-32-bytes")
    assert redirect is not None
    code_value = parse_qs(urlsplit(redirect).query)["code"][0]
    code = await provider.load_authorization_code(client, code_value)
    assert code is not None

    issued = await provider.exchange_authorization_code(client, code)
    access = await provider.load_access_token(issued.access_token)
    assert access is not None and access.subject == "owner"
    refresh = await provider.load_refresh_token(client, str(issued.refresh_token))
    assert refresh is not None
    rotated = await provider.exchange_refresh_token(client, refresh, ["driveops"])
    assert await provider.load_access_token(issued.access_token) is None
    assert await provider.load_refresh_token(client, str(issued.refresh_token)) is None
    assert await provider.load_access_token(rotated.access_token) is not None

    raw_db = (tmp_path / "oauth.db").read_bytes()
    assert issued.access_token.encode() not in raw_db
    assert str(issued.refresh_token).encode() not in raw_db
    assert code_value.encode() not in raw_db


@pytest.mark.asyncio
async def test_oauth_redirect_allowlist_is_exact(tmp_path: Path) -> None:
    callback = "https://client.example/callback"
    provider = SingleOwnerOAuthProvider(
        issuer_url="https://mcp.example.com",
        access_key="owner-access-key-with-at-least-32-bytes",
        allowed_redirect_uris={callback},
        store=OAuthStore(tmp_path / "oauth.db"),
    )
    client = OAuthClientInformationFull(
        client_id="client-1",
        redirect_uris=[f"{callback}/"],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope="driveops",
    )
    with pytest.raises(RegistrationError, match="not allowlisted"):
        await provider.register_client(client)


@pytest.mark.asyncio
async def test_remote_server_blocks_host_filesystem_paths() -> None:
    from mcp import Client

    async with Client(build_server(allow_local_file_access=False)) as client:
        result = await client.call_tool(
            "drive.download_file",
            {"file_id_or_name": "file-id", "output_path": "/tmp/private"},
        )
    assert result.is_error
    assert "server filesystem is disabled" in result.content[0].text
