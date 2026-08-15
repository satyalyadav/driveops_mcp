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


class AuthRefreshRejectedError(PermissionError):
    """Google permanently rejected a saved OAuth refresh token."""

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


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


def _client_project_id() -> str | None:
    secret = client_secret_path()
    if not secret.is_file():
        return None
    try:
        content = json.loads(secret.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(content, dict):
        return None
    for client_type in ("installed", "web"):
        client = content.get(client_type)
        if isinstance(client, dict) and isinstance(client.get("project_id"), str):
            return client["project_id"]
    return None


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
        "google_cloud_project_id": _client_project_id(),
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
        "credentials_verified_online": False,
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


def _refresh_error_details(exc: RefreshError) -> tuple[str | None, str | None]:
    error_code: str | None = None
    description: str | None = None
    for value in exc.args:
        if isinstance(value, dict):
            raw_code = value.get("error")
            raw_description = value.get("error_description")
            if isinstance(raw_code, str):
                error_code = raw_code
            if isinstance(raw_description, str):
                description = raw_description
    return error_code, description


def _refresh_rejected_error(
    exc: RefreshError, profile: ScopeProfile
) -> AuthRefreshRejectedError:
    error_code, description = _refresh_error_details(exc)
    detail = error_code or "refresh_rejected"
    if description:
        detail = f"{detail}: {description}"
    message = (
        f"Google rejected the saved OAuth refresh token ({detail}). "
        f"Run `driveops-mcp auth login --profile {profile} --force` to reconnect. "
        "If this recurs about every seven days, the Google OAuth app is probably "
        "External + Testing. In Google Cloud Console, open Google Auth Platform > "
        "Audience, change Publishing status to In production, and then reconnect "
        "once. Retrying the same rejected token cannot repair it."
    )
    return AuthRefreshRejectedError(message, error_code=error_code)


def check_credentials(profile: ScopeProfile | None = None) -> dict[str, object]:
    """Force an online refresh to verify that saved credentials are durable."""

    profile = profile or scope_profile()
    scopes = scopes_for_profile(profile)
    token = token_path()
    injected_token = token_json()
    status = auth_status(profile)

    try:
        if injected_token:
            creds = _credentials_from_json(injected_token, scopes)
        elif token.exists():
            creds = Credentials.from_authorized_user_file(str(token), scopes)
        else:
            return {
                **status,
                "check_status": "missing",
                "check_error": "No saved Google OAuth token was found.",
            }
    except (KeyError, TypeError, ValueError) as exc:
        return {
            **status,
            "check_status": "invalid",
            "check_error": f"The saved Google OAuth token is invalid: {exc}",
        }

    if not creds.has_scopes(scopes):
        return {
            **status,
            "check_status": "wrong_scopes",
            "check_error": (
                "The saved token does not contain the scopes required by the "
                f"{profile} profile."
            ),
        }
    if not creds.refresh_token:
        return {
            **status,
            "check_status": "not_refreshable",
            "check_error": (
                "The saved token has no refresh token and will stop working when "
                "its access token expires."
            ),
        }

    try:
        _refresh_expired_credentials(creds, Request())
    except AuthRefreshTransientError as exc:
        return {
            **status,
            "check_status": "network_error",
            "check_error": str(exc),
        }
    except RefreshError as exc:
        rejected = _refresh_rejected_error(exc, profile)
        return {
            **status,
            "check_status": "rejected",
            "refresh_error_code": rejected.error_code,
            "check_error": str(rejected),
        }

    if not injected_token:
        _write_private_text(token, creds.to_json())
    return {
        **auth_status(profile),
        "credentials_verified_online": True,
        "check_status": "ok",
        "check_error": None,
    }


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
        except RefreshError as exc:
            rejected = _refresh_rejected_error(
                exc, profile or scope_profile()
            )
            if injected_token:
                raise AuthRefreshRejectedError(
                    f"{rejected} Replace DRIVEOPS_GOOGLE_TOKEN_JSON before "
                    "restarting the service.",
                    error_code=rejected.error_code,
                ) from exc
            print(str(rejected), file=sys.stderr)
            token.unlink(missing_ok=True)
            creds = None

    if injected_token:
        raise PermissionError(
            "DRIVEOPS_GOOGLE_TOKEN_JSON is expired and cannot be refreshed. "
            "Rotate the hosted secret."
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
