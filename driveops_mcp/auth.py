"""Google OAuth and local path handling for DriveOps MCP."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Literal

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

ScopeProfile = Literal["readonly", "write"]

READONLY_SCOPES = [
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

WRITE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

AUTH_REFRESH_ATTEMPTS = 3
AUTH_REFRESH_RETRY_DELAYS_SECONDS = (0.5, 1.5)


class AuthRefreshTransientError(RuntimeError):
    pass


class _CommandBrowser(webbrowser.BaseBrowser):
    def __init__(self, command: list[str]) -> None:
        self.command = command

    def open(
        self,
        url: str,
        new: int = 0,
        autoraise: bool = True,
    ) -> bool:
        del new, autoraise
        subprocess.Popen(
            [*self.command, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True


def config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "driveops-mcp"


def state_dir() -> Path:
    if override := os.environ.get("DRIVEOPS_STATE_DIR"):
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "driveops-mcp"


def client_secret_path() -> Path:
    return Path(
        os.environ.get(
            "DRIVEOPS_GOOGLE_CLIENT_SECRET",
            config_dir() / "client_secret.json",
        )
    ).expanduser()


def token_path() -> Path:
    return Path(
        os.environ.get(
            "DRIVEOPS_GOOGLE_TOKEN",
            config_dir() / "token.json",
        )
    ).expanduser()


def token_json() -> str | None:
    """Return a hosted secret-injected Google token without writing it to disk."""

    return os.environ.get("DRIVEOPS_GOOGLE_TOKEN_JSON")


def credentials_configured() -> bool:
    return bool(token_json()) or token_path().is_file()


def credentials_ready(profile: ScopeProfile | None = None) -> bool:
    return bool(auth_status(profile)["credentials_ready"])


def _credentials_from_json(content: str, scopes: list[str]) -> Credentials:
    try:
        info = json.loads(content)
        if not isinstance(info, dict):
            raise ValueError
        return Credentials.from_authorized_user_info(info, scopes)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        raise ValueError(
            "DRIVEOPS_GOOGLE_TOKEN_JSON is not a valid authorized-user token."
        ) from exc


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _write_private_text(path: Path, content: str) -> None:
    path.touch(mode=0o600, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o600)
    path.write_text(content, encoding="utf-8")


def scope_profile() -> ScopeProfile:
    raw = os.environ.get("DRIVEOPS_SCOPE_PROFILE", "readonly").strip().lower()
    if raw in {"write", "full", "rw"}:
        return "write"
    return "readonly"


def scopes_for_profile(profile: ScopeProfile | None = None) -> list[str]:
    profile = profile or scope_profile()
    if profile == "write":
        return WRITE_SCOPES
    return READONLY_SCOPES


def _browser_name() -> str | None:
    if browser := os.environ.get("DRIVEOPS_BROWSER"):
        return browser
    if shutil.which("wslview"):
        webbrowser.register(
            "driveops-wslview",
            None,
            _CommandBrowser(["wslview"]),
            preferred=True,
        )
        return "driveops-wslview"
    if shutil.which("rundll32.exe"):
        webbrowser.register(
            "driveops-windows-url",
            None,
            _CommandBrowser(["rundll32.exe", "url.dll,FileProtocolHandler"]),
            preferred=True,
        )
        return "driveops-windows-url"
    if shutil.which("explorer.exe"):
        webbrowser.register(
            "driveops-windows",
            None,
            _CommandBrowser(["explorer.exe"]),
            preferred=True,
        )
        return "driveops-windows"
    return None


def profile_from_name(value: str | None) -> ScopeProfile:
    if value is None:
        return scope_profile()
    if value and value.strip().lower() in {"write", "full", "rw"}:
        return "write"
    return "readonly"


def require_write_profile() -> None:
    if scope_profile() != "write":
        raise PermissionError(
            "Write tools require DRIVEOPS_SCOPE_PROFILE=write. "
            "Keep the default readonly profile for search/read-only use."
        )


def auth_status(profile: ScopeProfile | None = None) -> dict[str, object]:
    profile = profile or scope_profile()
    token = token_path()
    secret = client_secret_path()
    token_valid = False
    token_expired = False
    has_required_scopes = False
    token_refreshable = False
    creds: Credentials | None = None
    injected_token = token_json()
    if injected_token:
        try:
            creds = _credentials_from_json(injected_token, scopes_for_profile(profile))
            token_valid = creds.valid
            has_required_scopes = creds.has_scopes(scopes_for_profile(profile))
        except (KeyError, TypeError, ValueError):
            token_valid = False
            has_required_scopes = False
    elif token.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(token), scopes_for_profile(profile)
            )
            token_valid = creds.valid
            has_required_scopes = creds.has_scopes(scopes_for_profile(profile))
        except (KeyError, TypeError, ValueError):
            token_valid = False
            has_required_scopes = False
    if creds is not None:
        token_expired = bool(creds.expired)
        token_refreshable = bool(creds.refresh_token)
    return {
        "profile": profile,
        "client_secret_path": str(secret),
        "client_secret_present": secret.exists(),
        "token_path": str(token),
        "token_present": bool(injected_token) or token.exists(),
        "token_source": "environment" if injected_token else "file",
        "token_valid": token_valid,
        "token_expired": token_expired,
        "token_refreshable": token_refreshable,
        "has_required_scopes": has_required_scopes,
        "credentials_ready": has_required_scopes and (token_valid or token_refreshable),
        "scopes": scopes_for_profile(profile),
    }


def logout() -> bool:
    token = token_path()
    existed = token.exists()
    token.unlink(missing_ok=True)
    return existed


def _refresh_expired_credentials(creds: Credentials, request: Request) -> None:
    for attempt in range(1, AUTH_REFRESH_ATTEMPTS + 1):
        try:
            creds.refresh(request)
            return
        except TransportError as exc:
            if attempt >= AUTH_REFRESH_ATTEMPTS:
                raise AuthRefreshTransientError(
                    "Google OAuth token refresh failed after "
                    f"{AUTH_REFRESH_ATTEMPTS} attempts due to a transient network "
                    "or DNS error. Check connectivity and retry."
                ) from exc
            delay = AUTH_REFRESH_RETRY_DELAYS_SECONDS[attempt - 1]
            print(
                "Google OAuth token refresh hit a transient network/DNS error; "
                f"retrying in {delay:g}s ({attempt + 1}/{AUTH_REFRESH_ATTEMPTS}).",
                file=sys.stderr,
            )
            time.sleep(delay)


def get_credentials(
    profile: ScopeProfile | None = None,
    *,
    force_reauth: bool = False,
    show_auth_url: bool = False,
) -> Credentials:
    """Load or mint Google OAuth credentials.

    This function is intentionally not called at import time. MCP clients can list
    tools without opening a browser or touching local secrets.
    """

    scopes = scopes_for_profile(profile)
    token = token_path()
    secret = client_secret_path()
    injected_token = token_json()
    if not injected_token:
        _secure_directory(token.parent)
        if token.exists() and os.name != "nt":
            token.chmod(0o600)

    creds: Credentials | None = None
    if force_reauth:
        if injected_token:
            raise ValueError(
                "Cannot force reauthentication while DRIVEOPS_GOOGLE_TOKEN_JSON is set."
            )
        token.unlink(missing_ok=True)
    elif injected_token:
        creds = _credentials_from_json(injected_token, scopes)
        if not creds.has_scopes(scopes):
            raise PermissionError(
                "DRIVEOPS_GOOGLE_TOKEN_JSON does not contain the scopes required by "
                f"the {profile or scope_profile()} profile."
            )
    elif token.exists():
        creds = Credentials.from_authorized_user_file(str(token), scopes)
        if not creds.has_scopes(scopes):
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            _refresh_expired_credentials(creds, Request())
            if not injected_token:
                _write_private_text(token, creds.to_json())
            return creds
        except RefreshError:
            if injected_token:
                raise PermissionError(
                    "The injected Google OAuth refresh token was rejected. Rotate "
                    "DRIVEOPS_GOOGLE_TOKEN_JSON before restarting the service."
                )
            token.unlink(missing_ok=True)
            creds = None

    if injected_token:
        raise PermissionError(
            "DRIVEOPS_GOOGLE_TOKEN_JSON is expired and cannot be refreshed. Rotate the hosted secret."
        )

    if not secret.exists():
        raise FileNotFoundError(
            f"Google OAuth client secret not found at {secret}. "
            "Set DRIVEOPS_GOOGLE_CLIENT_SECRET or place client_secret.json "
            "under ~/.config/driveops-mcp/."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(secret), scopes)
    print("Opening browser for Google authorization...", file=sys.stderr)
    prompt_message = "Please visit this URL to authorize DriveOps MCP: {url}"
    previous_relax_scope = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    try:
        creds = flow.run_local_server(
            port=0,
            authorization_prompt_message=prompt_message if show_auth_url else None,
            browser=_browser_name(),
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
        )
    finally:
        if previous_relax_scope is None:
            os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
        else:
            os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = previous_relax_scope
    _write_private_text(token, creds.to_json())
    return creds
