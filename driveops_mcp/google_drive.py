"""Concrete Google API backend for DriveOps."""

from __future__ import annotations

from typing import Any

from googleapiclient.discovery import build

from .auth import get_credentials
from .errors import AmbiguousFileError, AmbiguousFolderError, DriveOpsError
from .google_access import GoogleAccessMixin
from .google_files import GoogleFilesMixin


class GoogleDriveClient(GoogleFilesMixin, GoogleAccessMixin):
    def __init__(
        self, drive_service: Any | None = None, sheets_service: Any | None = None
    ) -> None:
        if drive_service is None:
            creds = get_credentials()
            drive_service = build("drive", "v3", credentials=creds)
            sheets_service = build("sheets", "v4", credentials=creds)
        self.drive = drive_service
        self.sheets = sheets_service


__all__ = [
    "AmbiguousFileError",
    "AmbiguousFolderError",
    "DriveOpsError",
    "GoogleDriveClient",
]
