"""Single-owner OAuth 2.1 provider for hosted DriveOps MCP deployments."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizeError,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .auth import state_dir
from .http_security import secret_is_strong

OAUTH_SCOPES = ["driveops"]
AUTHORIZATION_TTL_SECONDS = 10 * 60
ACCESS_TOKEN_TTL_SECONDS = 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_REGISTERED_CLIENTS = 50


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class OAuthStore:
    """Persist OAuth clients and opaque token metadata in an owner-only SQLite DB."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or state_dir() / "oauth.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.db_path.parent.chmod(0o700)
        self.db_path.touch(mode=0o600, exist_ok=True)
        if os.name != "nt":
            self.db_path.chmod(0o600)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout = 10000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists oauth_clients (
                    client_id text primary key,
                    client_json text not null,
                    created_at integer not null
                );
                create table if not exists oauth_pending (
                    request_hash text primary key,
                    request_json text not null,
                    expires_at integer not null,
                    attempts integer not null default 0
                );
                create table if not exists oauth_codes (
                    code_hash text primary key,
                    code_json text not null,
                    expires_at integer not null
                );
                create table if not exists oauth_access_tokens (
                    token_hash text primary key,
                    token_json text not null,
                    family_id text not null,
                    expires_at integer not null
                );
                create table if not exists oauth_refresh_tokens (
                    token_hash text primary key,
                    token_json text not null,
                    family_id text not null,
                    expires_at integer not null
                );
                create index if not exists oauth_access_family
                    on oauth_access_tokens(family_id);
                create index if not exists oauth_refresh_family
                    on oauth_refresh_tokens(family_id);
                create table if not exists oauth_events (
                    id text primary key,
                    event text not null,
                    client_id text,
                    status text not null,
                    created_at integer not null
                );
                """
            )

    def event(self, event: str, status: str, client_id: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into oauth_events(id, event, client_id, status, created_at) values (?, ?, ?, ?, ?)",
                (secrets.token_urlsafe(18), event, client_id, status, int(time.time())),
            )

    def cleanup(self) -> None:
        now = int(time.time())
        with self._connect() as conn:
            for table in (
                "oauth_pending",
                "oauth_codes",
                "oauth_access_tokens",
                "oauth_refresh_tokens",
            ):
                conn.execute(f"delete from {table} where expires_at <= ?", (now,))


class SingleOwnerOAuthProvider:
    """OAuth provider that grants one configured owner access to one Drive account."""

    def __init__(
        self,
        *,
        issuer_url: str,
        access_key: str,
        allowed_redirect_uris: set[str],
        store: OAuthStore | None = None,
    ) -> None:
        if not secret_is_strong(access_key):
            raise ValueError("DRIVEOPS_OAUTH_ACCESS_KEY must be at least 32 bytes.")
        if not allowed_redirect_uris:
            raise ValueError(
                "DRIVEOPS_OAUTH_ALLOWED_REDIRECT_URIS must contain at least one exact callback URL."
            )
        self.issuer_url = issuer_url.rstrip("/")
        self.resource_url = f"{self.issuer_url}/mcp"
        self._access_key = access_key.encode("utf-8")
        self.allowed_redirect_uris = set(allowed_redirect_uris)
        self.store = store or OAuthStore()

    def _validate_redirect_uri(self, uri: str) -> None:
        parsed = urlsplit(uri)
        is_loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
            raise RegistrationError(
                "invalid_redirect_uri",
                "Redirect URIs must use HTTPS, except loopback callbacks.",
            )
        if uri not in self.allowed_redirect_uris:
            raise RegistrationError(
                "invalid_redirect_uri",
                "The callback URL is not allowlisted by this DriveOps deployment.",
            )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self.store._connect() as conn:
            row = conn.execute(
                "select client_json from oauth_clients where client_id = ?",
                (client_id,),
            ).fetchone()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row["client_json"])

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise RegistrationError("invalid_client_metadata", "client_id is required")
        if client_info.token_endpoint_auth_method not in {
            "none",
            "client_secret_post",
            "client_secret_basic",
        }:
            raise RegistrationError(
                "invalid_client_metadata",
                "Unsupported token endpoint authentication method.",
            )
        for redirect_uri in client_info.redirect_uris or []:
            self._validate_redirect_uri(str(redirect_uri))
        with self.store._connect() as conn:
            count = conn.execute("select count(*) from oauth_clients").fetchone()[0]
            if count >= MAX_REGISTERED_CLIENTS:
                raise RegistrationError(
                    "invalid_client_metadata",
                    "This deployment has reached its client limit.",
                )
            conn.execute(
                "insert into oauth_clients(client_id, client_json, created_at) values (?, ?, ?)",
                (
                    client_info.client_id,
                    client_info.model_dump_json(),
                    int(time.time()),
                ),
            )
        self.store.event("client_registered", "success", client_info.client_id)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not client.client_id:
            raise AuthorizeError("unauthorized_client", "client_id is required")
        if params.resource and params.resource.rstrip("/") != self.resource_url:
            raise AuthorizeError("invalid_target", "Unexpected resource indicator.")
        request_id = secrets.token_urlsafe(32)
        payload = {
            "client_id": client.client_id,
            "client_name": client.client_name or "MCP client",
            "params": params.model_dump(mode="json"),
        }
        expires_at = int(time.time()) + AUTHORIZATION_TTL_SECONDS
        with self.store._connect() as conn:
            conn.execute(
                "insert into oauth_pending(request_hash, request_json, expires_at) values (?, ?, ?)",
                (_token_hash(request_id), json.dumps(payload), expires_at),
            )
        return f"{self.issuer_url}/oauth/authorize?request={request_id}"

    def pending_request(self, request_id: str) -> dict[str, Any] | None:
        self.store.cleanup()
        with self.store._connect() as conn:
            row = conn.execute(
                "select request_json, attempts from oauth_pending where request_hash = ?",
                (_token_hash(request_id),),
            ).fetchone()
        if row is None or row["attempts"] >= 5:
            return None
        return json.loads(row["request_json"])

    def approve(self, request_id: str, access_key: str) -> str | None:
        request_hash = _token_hash(request_id)
        denied_client_id: str | None = None
        with self.store._connect() as conn:
            conn.execute("begin immediate")
            row = conn.execute(
                "select request_json, expires_at, attempts from oauth_pending where request_hash = ?",
                (request_hash,),
            ).fetchone()
            if (
                row is None
                or row["expires_at"] <= int(time.time())
                or row["attempts"] >= 5
            ):
                return None
            if not hmac.compare_digest(access_key.encode("utf-8"), self._access_key):
                denied_client_id = json.loads(row["request_json"])["client_id"]
                conn.execute(
                    "update oauth_pending set attempts = attempts + 1 where request_hash = ?",
                    (request_hash,),
                )
            else:
                payload = json.loads(row["request_json"])
                params = AuthorizationParams.model_validate(payload["params"])
                code_value = secrets.token_urlsafe(32)
                code = AuthorizationCode(
                    code=code_value,
                    scopes=params.scopes or OAUTH_SCOPES,
                    expires_at=time.time() + AUTHORIZATION_TTL_SECONDS,
                    client_id=payload["client_id"],
                    code_challenge=params.code_challenge,
                    redirect_uri=params.redirect_uri,
                    redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                    resource=params.resource,
                    subject="owner",
                )
                conn.execute(
                    "insert into oauth_codes(code_hash, code_json, expires_at) values (?, ?, ?)",
                    (
                        _token_hash(code_value),
                        code.model_copy(update={"code": ""}).model_dump_json(),
                        int(code.expires_at),
                    ),
                )
                conn.execute(
                    "delete from oauth_pending where request_hash = ?", (request_hash,)
                )
        if denied_client_id:
            self.store.event("authorization", "denied", denied_client_id)
            return None
        self.store.event("authorization", "approved", payload["client_id"])
        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code_value,
            state=params.state,
            iss=self.issuer_url,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        del client
        self.store.cleanup()
        with self.store._connect() as conn:
            row = conn.execute(
                "select code_json from oauth_codes where code_hash = ?",
                (_token_hash(authorization_code),),
            ).fetchone()
        if row is None:
            return None
        stored = AuthorizationCode.model_validate_json(row["code_json"])
        return stored.model_copy(update={"code": authorization_code})

    def _mint_tokens(
        self,
        *,
        client_id: str,
        scopes: list[str],
        subject: str | None,
        resource: str | None,
        family_id: str | None = None,
    ) -> OAuthToken:
        now = int(time.time())
        access_value = secrets.token_urlsafe(32)
        refresh_value = secrets.token_urlsafe(32)
        family_id = family_id or secrets.token_urlsafe(24)
        access = AccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
            resource=resource,
            subject=subject,
            claims={"iss": self.issuer_url},
        )
        refresh = RefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
            subject=subject,
        )
        with self.store._connect() as conn:
            conn.execute(
                "insert into oauth_access_tokens(token_hash, token_json, family_id, expires_at) values (?, ?, ?, ?)",
                (
                    _token_hash(access_value),
                    access.model_copy(update={"token": ""}).model_dump_json(),
                    family_id,
                    access.expires_at,
                ),
            )
            conn.execute(
                "insert into oauth_refresh_tokens(token_hash, token_json, family_id, expires_at) values (?, ?, ?, ?)",
                (
                    _token_hash(refresh_value),
                    refresh.model_copy(update={"token": ""}).model_dump_json(),
                    family_id,
                    refresh.expires_at,
                ),
            )
        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(scopes),
            refresh_token=refresh_value,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if not client.client_id or client.client_id != authorization_code.client_id:
            raise TokenError("invalid_grant", "Authorization code client mismatch.")
        with self.store._connect() as conn:
            cursor = conn.execute(
                "delete from oauth_codes where code_hash = ?",
                (_token_hash(authorization_code.code),),
            )
            if cursor.rowcount != 1:
                raise TokenError(
                    "invalid_grant", "Authorization code was already used."
                )
        result = self._mint_tokens(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            subject=authorization_code.subject,
            resource=authorization_code.resource,
        )
        self.store.event("token_issued", "success", client.client_id)
        return result

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        self.store.cleanup()
        with self.store._connect() as conn:
            row = conn.execute(
                "select token_json from oauth_refresh_tokens where token_hash = ?",
                (_token_hash(refresh_token),),
            ).fetchone()
        token = (
            RefreshToken.model_validate_json(row["token_json"]).model_copy(
                update={"token": refresh_token}
            )
            if row
            else None
        )
        if token and token.client_id != client.client_id:
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        token_hash = _token_hash(refresh_token.token)
        with self.store._connect() as conn:
            conn.execute("begin immediate")
            row = conn.execute(
                "select family_id from oauth_refresh_tokens where token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                raise TokenError("invalid_grant", "Refresh token was already used.")
            family_id = row["family_id"]
            conn.execute(
                "delete from oauth_refresh_tokens where family_id = ?", (family_id,)
            )
            conn.execute(
                "delete from oauth_access_tokens where family_id = ?", (family_id,)
            )
        result = self._mint_tokens(
            client_id=str(client.client_id),
            scopes=scopes,
            subject=refresh_token.subject,
            resource=self.resource_url,
            family_id=family_id,
        )
        self.store.event("token_refreshed", "success", str(client.client_id))
        return result

    async def load_access_token(self, token: str) -> AccessToken | None:
        self.store.cleanup()
        with self.store._connect() as conn:
            row = conn.execute(
                "select token_json from oauth_access_tokens where token_hash = ?",
                (_token_hash(token),),
            ).fetchone()
        if row is None:
            return None
        return AccessToken.model_validate_json(row["token_json"]).model_copy(
            update={"token": token}
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        table = (
            "oauth_access_tokens"
            if isinstance(token, AccessToken)
            else "oauth_refresh_tokens"
        )
        with self.store._connect() as conn:
            row = conn.execute(
                f"select family_id from {table} where token_hash = ?",
                (_token_hash(token.token),),
            ).fetchone()
            if row:
                conn.execute(
                    "delete from oauth_access_tokens where family_id = ?",
                    (row["family_id"],),
                )
                conn.execute(
                    "delete from oauth_refresh_tokens where family_id = ?",
                    (row["family_id"],),
                )
        if row:
            self.store.event("token_revoked", "success", token.client_id)


def register_owner_authorization_routes(
    mcp: Any, provider: SingleOwnerOAuthProvider
) -> None:
    @mcp.custom_route("/oauth/authorize", methods=["GET", "POST"])
    async def owner_authorization(request: Request) -> Response:
        if request.method == "POST":
            form = await request.form()
            request_id = str(form.get("request", ""))
            access_key = str(form.get("access_key", ""))
            redirect = provider.approve(request_id, access_key)
            if redirect:
                return RedirectResponse(redirect, status_code=303)
            return HTMLResponse(
                _authorization_page(
                    None, request_id, error="Invalid or expired authorization request."
                ),
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )

        request_id = request.query_params.get("request", "")
        pending = provider.pending_request(request_id)
        if pending is None:
            return HTMLResponse(
                _authorization_page(
                    None, request_id, error="Invalid or expired authorization request."
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        return HTMLResponse(
            _authorization_page(pending, request_id),
            headers={"Cache-Control": "no-store"},
        )


def _authorization_page(
    pending: dict[str, Any] | None, request_id: str, *, error: str | None = None
) -> str:
    client_name = html.escape(str((pending or {}).get("client_name", "Unknown client")))
    params = (pending or {}).get("params", {})
    redirect_uri = html.escape(str(params.get("redirect_uri", "")))
    scopes = html.escape(" ".join(params.get("scopes") or OAUTH_SCOPES))
    safe_request = html.escape(request_id, quote=True)
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    disabled = " disabled" if pending is None else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Authorize DriveOps MCP</title><style>
body{{font:16px system-ui,sans-serif;max-width:42rem;margin:4rem auto;padding:0 1rem;color:#202124}}
main{{border:1px solid #dadce0;border-radius:12px;padding:1.5rem}}code{{overflow-wrap:anywhere}}
label{{display:block;margin:1.25rem 0 .4rem}}input{{box-sizing:border-box;width:100%;padding:.7rem}}
button{{margin-top:1rem;padding:.7rem 1rem}}.error{{color:#b3261e}}.notice{{background:#f1f3f4;padding:.8rem}}
</style></head><body><main><h1>Authorize DriveOps MCP</h1>{error_html}
<p><strong>{client_name}</strong> is requesting access to the Google Drive account connected to this server.</p>
<p>Scope: <code>{scopes}</code><br>Callback: <code>{redirect_uri}</code></p>
<p class="notice">Only continue if you started this connection. This deployment is single-owner: approval grants access to the server owner's Drive.</p>
<form method="post"><input type="hidden" name="request" value="{safe_request}">
<label for="access_key">Owner access key</label><input id="access_key" name="access_key" type="password" autocomplete="current-password" required{disabled}>
<button type="submit"{disabled}>Authorize</button></form></main></body></html>"""
