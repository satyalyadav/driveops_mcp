"""Google OAuth and local path handling for DriveOps MCP."""

from __future__ import annotations

import os
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
    has_required_scopes = False
    if token.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(token), scopes_for_profile(profile)
            )
            token_valid = creds.valid
            has_required_scopes = creds.has_scopes(scopes_for_profile(profile))
        except ValueError:
            token_valid = False
            has_required_scopes = False
    return {
        "profile": profile,
        "client_secret_path": str(secret),
        "client_secret_present": secret.exists(),
        "token_path": str(token),
        "token_present": token.exists(),
        "token_valid": token_valid,
        "has_required_scopes": has_required_scopes,
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
    token.parent.mkdir(parents=True, exist_ok=True)

    creds: Credentials | None = None
    if force_reauth:
        token.unlink(missing_ok=True)
    elif token.exists():
        creds = Credentials.from_authorized_user_file(str(token), scopes)
        if not creds.has_scopes(scopes):
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            _refresh_expired_credentials(creds, Request())
            token.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except RefreshError:
            token.unlink(missing_ok=True)
            creds = None

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
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds
