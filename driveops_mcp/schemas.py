"""Shared schema helpers and constants."""

from __future__ import annotations

from typing import Literal

OrganizationStrategy = Literal[
    "by_created_month",
    "by_modified_month",
    "by_mime_type",
    "by_name_prefix",
]

PLAN_STATUSES = {"planned", "applied", "failed", "undone"}

GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDE_MIME = "application/vnd.google-apps.presentation"


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def confirmation_for(plan_id: str) -> str:
    return f"APPLY-{plan_id[:8]}"


def undo_confirmation_for(plan_id: str) -> str:
    return f"UNDO-{plan_id[:8]}"


def normalize_file(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "mimeType": item.get("mimeType"),
        "createdTime": item.get("createdTime"),
        "modifiedTime": item.get("modifiedTime"),
        "size": item.get("size"),
        "parents": item.get("parents", []),
        "webViewLink": item.get("webViewLink"),
        "webContentLink": item.get("webContentLink"),
        "folderPath": item.get("folderPath"),
        "folderPaths": item.get("folderPaths", []),
    }
