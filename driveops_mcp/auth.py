"""Google OAuth and local path handling for DriveOps MCP."""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from typing import Literal

from google.auth.exceptions import RefreshError
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


def require_write_profile() -> None:
    if scope_profile() != "write":
        raise PermissionError(
            "Write tools require DRIVEOPS_SCOPE_PROFILE=write. "
            "Keep the default readonly profile for search/read-only use."
        )


def get_credentials(profile: ScopeProfile | None = None) -> Credentials:
    """Load or mint Google OAuth credentials.

    This function is intentionally not called at import time. MCP clients can list
    tools without opening a browser or touching local secrets.
    """

    scopes = scopes_for_profile(profile)
    token = token_path()
    secret = client_secret_path()
    token.parent.mkdir(parents=True, exist_ok=True)

    creds: Credentials | None = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), scopes)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
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
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes=True,
    )
    print("Opening browser for Google authorization...")
    print(f"If it does not open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)
    creds = flow.run_local_server(port=0)
    token.write_text(creds.to_json(), encoding="utf-8")
    return creds
