"""Shared schema helpers and constants."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OrganizationStrategy = Literal[
    "by_created_month",
    "by_modified_month",
    "by_mime_type",
    "by_name_prefix",
]

PLAN_STATUSES = {"planned", "applied", "partially_applied", "failed", "undone"}

GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDE_MIME = "application/vnd.google-apps.presentation"


class DriveFileAction(BaseModel):
    """Discoverable schema for a single safe general Drive action."""

    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "create_folder",
        "create_file",
        "upload_file",
        "rename_file",
        "copy_file",
        "move_file",
        "trash_file",
        "restore_file",
        "delete_file",
        "share_file",
        "update_permission",
        "remove_permission",
    ] = Field(description="Action to preview and later apply.")
    file_id_or_name: str | None = Field(
        default=None, description="Existing file/folder ID or unambiguous name."
    )
    name: str | None = Field(default=None, description="Name for a new file or folder.")
    parent_id_or_name: str | None = Field(
        default=None,
        description="Parent folder for create, upload, or copy; defaults to My Drive.",
    )
    target_folder_id_or_name: str | None = Field(
        default=None, description="Destination folder for move_file."
    )
    source_parent_id: str | None = Field(
        default=None, description="Optional current parent to remove during move_file."
    )
    new_name: str | None = Field(
        default=None, description="New name for rename_file or copy_file."
    )
    mime_type: str | None = Field(
        default=None, description="MIME type for a created/uploaded file."
    )
    text: str | None = Field(default=None, description="UTF-8 content for create_file.")
    content_base64: str | None = Field(
        default=None, description="Base64 content for create_file."
    )
    local_path: str | None = Field(
        default=None, description="File path on the MCP server host for upload_file."
    )
    permission_id: str | None = Field(
        default=None,
        description="Permission ID for update_permission or remove_permission.",
    )
    permission_type: Literal["user", "group", "domain", "anyone"] | None = Field(
        default=None, description="Audience type for share_file; defaults to user."
    )
    role: (
        Literal["reader", "commenter", "writer", "fileOrganizer", "organizer", "owner"]
        | None
    ) = Field(default=None, description="Drive permission role.")
    email_address: str | None = Field(
        default=None, description="User or group email for share_file."
    )
    domain: str | None = Field(default=None, description="Domain for a domain share.")
    allow_file_discovery: bool | None = Field(
        default=None, description="Whether domain/anyone shares are discoverable."
    )
    send_notification_email: bool = Field(
        default=True,
        description="Send Google's sharing notification for user/group shares.",
    )


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
