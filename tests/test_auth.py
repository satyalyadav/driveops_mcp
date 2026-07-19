from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from driveops_mcp import auth


def test_get_credentials_uses_google_lowercase_include_granted_scopes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "client_secret.json"
    token = tmp_path / "token.json"
    secret.write_text('{"installed": {"client_id": "id", "client_secret": "secret"}}')
    monkeypatch.setenv("DRIVEOPS_GOOGLE_CLIENT_SECRET", str(secret))
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))
    monkeypatch.setattr(auth, "_browser_name", lambda: "test-browser")

    seen_kwargs = {}
    monkeypatch.delenv("OAUTHLIB_RELAX_TOKEN_SCOPE", raising=False)

    class FakeCredentials:
        valid = True

        def to_json(self) -> str:
            return '{"token": "fake"}'

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            assert path == str(secret)
            assert scopes == auth.READONLY_SCOPES
            return cls()

        def run_local_server(self, **kwargs):
            seen_kwargs.update(kwargs)
            assert auth.os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] == "1"
            return FakeCredentials()

    monkeypatch.setattr(auth, "InstalledAppFlow", FakeFlow)

    creds = auth.get_credentials()

    assert isinstance(creds, FakeCredentials)
    assert seen_kwargs["include_granted_scopes"] == "true"
    assert seen_kwargs["browser"] == "test-browser"
    assert token.read_text() == '{"token": "fake"}'
    if os.name != "nt":
        assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert "OAUTHLIB_RELAX_TOKEN_SCOPE" not in auth.os.environ


def test_get_credentials_restores_existing_oauthlib_scope_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "client_secret.json"
    token = tmp_path / "token.json"
    secret.write_text('{"installed": {"client_id": "id", "client_secret": "secret"}}')
    monkeypatch.setenv("DRIVEOPS_GOOGLE_CLIENT_SECRET", str(secret))
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))
    monkeypatch.setenv("OAUTHLIB_RELAX_TOKEN_SCOPE", "existing")

    class FakeCredentials:
        valid = True

        def to_json(self) -> str:
            return '{"token": "fake"}'

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            return cls()

        def run_local_server(self, **kwargs):
            assert auth.os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] == "1"
            return FakeCredentials()

    monkeypatch.setattr(auth, "InstalledAppFlow", FakeFlow)

    auth.get_credentials()

    assert auth.os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] == "existing"


def test_get_credentials_retries_transient_refresh_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    token = tmp_path / "token.json"
    token.write_text('{"token": "old"}')
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))

    sleeps = []
    monkeypatch.setattr(auth.time, "sleep", lambda delay: sleeps.append(delay))

    class FakeCredentials:
        valid = False
        expired = True
        refresh_token = "refresh-token"

        def __init__(self) -> None:
            self.refresh_calls = 0

        @classmethod
        def from_authorized_user_file(cls, path, scopes):
            assert path == str(token)
            assert scopes == auth.READONLY_SCOPES
            return fake_creds

        def has_scopes(self, scopes):
            return scopes == auth.READONLY_SCOPES

        def refresh(self, request):
            self.refresh_calls += 1
            if self.refresh_calls == 1:
                raise auth.TransportError("temporary DNS failure")
            self.valid = True
            self.expired = False

        def to_json(self) -> str:
            return '{"token": "new"}'

    fake_creds = FakeCredentials()
    monkeypatch.setattr(auth, "Credentials", FakeCredentials)

    creds = auth.get_credentials()

    assert creds is fake_creds
    assert fake_creds.refresh_calls == 2
    assert sleeps == [0.5]
    assert token.read_text() == '{"token": "new"}'


def test_get_credentials_keeps_token_after_transient_refresh_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    token = tmp_path / "token.json"
    token.write_text('{"token": "old"}')
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))
    monkeypatch.setattr(auth.time, "sleep", lambda delay: None)

    class FakeCredentials:
        valid = False
        expired = True
        refresh_token = "refresh-token"
        refresh_calls = 0

        @classmethod
        def from_authorized_user_file(cls, path, scopes):
            return cls()

        def has_scopes(self, scopes):
            return True

        def refresh(self, request):
            type(self).refresh_calls += 1
            raise auth.TransportError("temporary DNS failure")

    monkeypatch.setattr(auth, "Credentials", FakeCredentials)

    with pytest.raises(auth.AuthRefreshTransientError, match="failed after 3 attempts"):
        auth.get_credentials()

    assert FakeCredentials.refresh_calls == 3
    assert token.read_text() == '{"token": "old"}'


def test_browser_name_prefers_wslview(monkeypatch) -> None:
    monkeypatch.delenv("DRIVEOPS_BROWSER", raising=False)
    monkeypatch.setattr(
        auth.shutil,
        "which",
        lambda command: "/usr/bin/wslview" if command == "wslview" else None,
    )

    assert auth._browser_name() == "driveops-wslview"


def test_browser_name_uses_windows_url_handler(monkeypatch) -> None:
    monkeypatch.delenv("DRIVEOPS_BROWSER", raising=False)

    def fake_which(command: str) -> str | None:
        if command == "rundll32.exe":
            return "/mnt/c/Windows/system32/rundll32.exe"
        return None

    monkeypatch.setattr(auth.shutil, "which", fake_which)

    assert auth._browser_name() == "driveops-windows-url"
