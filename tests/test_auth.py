from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from driveops_mcp import auth, server


def test_google_scopes_only_request_drive_access() -> None:
    assert all("spreadsheets" not in scope for scope in auth.READONLY_SCOPES)
    assert all("spreadsheets" not in scope for scope in auth.WRITE_SCOPES)


def test_profile_from_name_uses_effective_environment_profile(monkeypatch) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")

    assert auth.profile_from_name(None) == "write"

    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "readonly")

    assert auth.profile_from_name(None) == "readonly"


def test_profile_from_name_explicit_value_overrides_environment(monkeypatch) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")

    assert auth.profile_from_name("readonly") == "readonly"

    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "readonly")

    assert auth.profile_from_name("write") == "write"


def test_auth_cli_uses_environment_profile_when_flag_is_omitted(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    calls = []

    def fake_get_credentials(profile, **kwargs):
        calls.append(("login", profile, kwargs))

    def fake_auth_status(profile):
        calls.append(("status", profile))
        return {"profile": profile}

    monkeypatch.setattr(server, "get_credentials", fake_get_credentials)
    monkeypatch.setattr(server, "auth_status", fake_auth_status)

    server.main(["auth", "login"])

    assert calls == [
        ("login", "write", {"force_reauth": False, "show_auth_url": True}),
        ("status", "write"),
    ]
    assert '"profile": "write"' in capsys.readouterr().out


def test_auth_status_reports_refresh_capability_for_valid_token(
    monkeypatch, tmp_path: Path
) -> None:
    token = tmp_path / "token.json"
    token.write_text('{"token": "valid"}')
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))

    class FakeCredentials:
        valid = True
        expired = False
        refresh_token = "refresh-token"

        @classmethod
        def from_authorized_user_file(cls, path, scopes):
            assert path == str(token)
            assert scopes == auth.READONLY_SCOPES
            return cls()

        def has_scopes(self, scopes):
            return scopes == auth.READONLY_SCOPES

    monkeypatch.setattr(auth, "Credentials", FakeCredentials)

    status = auth.auth_status("readonly")

    assert status["token_valid"] is True
    assert status["token_expired"] is False
    assert status["token_refreshable"] is True
    assert status["credentials_ready"] is True


def test_auth_status_reports_expired_refreshable_token_as_ready(
    monkeypatch, tmp_path: Path
) -> None:
    token = tmp_path / "token.json"
    token.write_text('{"token": "expired"}')
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))

    class FakeCredentials:
        valid = False
        expired = True
        refresh_token = "refresh-token"

        @classmethod
        def from_authorized_user_file(cls, path, scopes):
            return cls()

        def has_scopes(self, scopes):
            return scopes == auth.WRITE_SCOPES

    monkeypatch.setattr(auth, "Credentials", FakeCredentials)

    status = auth.auth_status("write")

    assert status["token_valid"] is False
    assert status["token_expired"] is True
    assert status["token_refreshable"] is True
    assert status["credentials_ready"] is True
    assert status["credentials_verified_online"] is False


def test_auth_status_reports_google_cloud_project_without_exposing_secret(
    monkeypatch, tmp_path: Path
) -> None:
    secret = tmp_path / "client_secret.json"
    secret.write_text(
        '{"installed":{"project_id":"durable-oauth-project",'
        '"client_secret":"do-not-return"}}'
    )
    monkeypatch.setenv("DRIVEOPS_GOOGLE_CLIENT_SECRET", str(secret))

    status = auth.auth_status("readonly")

    assert status["google_cloud_project_id"] == "durable-oauth-project"
    assert "do-not-return" not in str(status)


def test_check_credentials_forces_online_refresh_and_persists_token(
    monkeypatch, tmp_path: Path
) -> None:
    token = tmp_path / "token.json"
    token.write_text('{"token": "old"}')
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))

    class FakeCredentials:
        valid = True
        expired = False
        refresh_token = "refresh-token"

        @classmethod
        def from_authorized_user_file(cls, path, scopes):
            assert path == str(token)
            assert scopes == auth.WRITE_SCOPES
            return cls()

        def has_scopes(self, scopes):
            return scopes == auth.WRITE_SCOPES

        def refresh(self, request):
            self.valid = True
            self.expired = False

        def to_json(self):
            return '{"token": "fresh"}'

    monkeypatch.setattr(auth, "Credentials", FakeCredentials)

    result = auth.check_credentials("write")

    assert result["check_status"] == "ok"
    assert result["credentials_verified_online"] is True
    assert token.read_text() == '{"token": "fresh"}'


def test_check_credentials_explains_seven_day_refresh_rejection_and_keeps_token(
    monkeypatch, tmp_path: Path
) -> None:
    token = tmp_path / "token.json"
    token.write_text('{"token": "old"}')
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))

    class FakeCredentials:
        valid = False
        expired = True
        refresh_token = "refresh-token"

        @classmethod
        def from_authorized_user_file(cls, path, scopes):
            return cls()

        def has_scopes(self, scopes):
            return True

        def refresh(self, request):
            raise auth.RefreshError(
                "invalid_grant: Bad Request",
                {
                    "error": "invalid_grant",
                    "error_description": "Token has been expired or revoked.",
                },
            )

    monkeypatch.setattr(auth, "Credentials", FakeCredentials)

    result = auth.check_credentials("write")

    assert result["check_status"] == "rejected"
    assert result["refresh_error_code"] == "invalid_grant"
    assert "every seven days" in result["check_error"]
    assert "Publishing status to In production" in result["check_error"]
    assert token.read_text() == '{"token": "old"}'


def test_auth_check_cli_uses_environment_profile_and_fails_when_rejected(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    calls = []

    def fake_check_credentials(profile):
        calls.append(profile)
        return {"check_status": "rejected"}

    monkeypatch.setattr(server, "check_credentials", fake_check_credentials)

    with pytest.raises(SystemExit) as exc_info:
        server.main(["auth", "check"])

    assert exc_info.value.code == 1
    assert calls == ["write"]
    assert '"check_status": "rejected"' in capsys.readouterr().out


def test_get_credentials_supports_injected_hosted_token(monkeypatch) -> None:
    injected = (
        '{"token":"access","refresh_token":"refresh","client_id":"id",'
        '"client_secret":"secret"}'
    )
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN_JSON", injected)

    class FakeCredentials:
        valid = True
        expired = False
        refresh_token = "refresh"

        @classmethod
        def from_authorized_user_info(cls, info, scopes):
            assert info["refresh_token"] == "refresh"
            assert scopes == auth.READONLY_SCOPES
            return cls()

        def has_scopes(self, scopes):
            return scopes == auth.READONLY_SCOPES

    monkeypatch.setattr(auth, "Credentials", FakeCredentials)

    assert isinstance(auth.get_credentials(), FakeCredentials)
    assert auth.credentials_configured()


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


def test_force_reauth_keeps_existing_token_when_browser_flow_fails(
    monkeypatch, tmp_path: Path
) -> None:
    secret = tmp_path / "client_secret.json"
    token = tmp_path / "token.json"
    secret.write_text('{"installed": {"client_id": "id", "client_secret": "secret"}}')
    token.write_text('{"token": "still-usable"}')
    monkeypatch.setenv("DRIVEOPS_GOOGLE_CLIENT_SECRET", str(secret))
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            return cls()

        def run_local_server(self, **kwargs):
            raise RuntimeError("browser authorization failed")

    monkeypatch.setattr(auth, "InstalledAppFlow", FakeFlow)

    with pytest.raises(RuntimeError, match="browser authorization failed"):
        auth.get_credentials("write", force_reauth=True)

    assert token.read_text() == '{"token": "still-usable"}'


def test_force_reauth_replaces_existing_token_only_after_success(
    monkeypatch, tmp_path: Path
) -> None:
    secret = tmp_path / "client_secret.json"
    token = tmp_path / "token.json"
    secret.write_text('{"installed": {"client_id": "id", "client_secret": "secret"}}')
    token.write_text('{"token": "old"}')
    monkeypatch.setenv("DRIVEOPS_GOOGLE_CLIENT_SECRET", str(secret))
    monkeypatch.setenv("DRIVEOPS_GOOGLE_TOKEN", str(token))

    class FakeCredentials:
        def to_json(self):
            return '{"token": "new"}'

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            return cls()

        def run_local_server(self, **kwargs):
            assert token.read_text() == '{"token": "old"}'
            return FakeCredentials()

    monkeypatch.setattr(auth, "InstalledAppFlow", FakeFlow)

    auth.get_credentials("write", force_reauth=True)

    assert token.read_text() == '{"token": "new"}'


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
