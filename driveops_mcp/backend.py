"""Backend contract required by DriveOps workflows and MCP tools."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DriveBackend(Protocol):
    """Drive primitives used by the transport-independent safety layer."""

    def resolve_file(self, file_id_or_name: str) -> dict[str, Any]: ...

    def resolve_file_exact(self, file_id_or_name: str) -> dict[str, Any]: ...

    def resolve_folder(self, folder_id_or_name: str) -> dict[str, Any]: ...

    def resolve_folder_exact(self, folder_id_or_name: str) -> dict[str, Any]: ...

    def get_file(self, file_id: str, fields: str | None = None) -> dict[str, Any]: ...

    def search_files(
        self,
        *,
        query: str,
        folder_id: str | None = None,
        mime_types: list[str] | None = None,
        page_size: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]: ...

    def list_folder(
        self,
        folder_id_or_name: str,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]: ...

    def get_changes(
        self,
        folder_id_or_name: str,
        since: str,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]: ...

    def read_file(
        self,
        file_id_or_name: str,
        export_format: str | None = None,
        max_text_bytes: int = 200_000,
    ) -> dict[str, Any]: ...

    def download_file(
        self,
        file_id_or_name: str,
        *,
        export_format: str | None = None,
        output_path: str | None = None,
        max_bytes: int = 25_000_000,
        overwrite: bool = False,
    ) -> dict[str, Any]: ...

    def extract_file(
        self,
        file_id_or_name: str,
        *,
        output_dir: str | None = None,
        max_bytes: int = 50_000_000,
        max_text_chars: int = 500_000,
        overwrite: bool = False,
    ) -> dict[str, Any]: ...

    def create_folder(self, name: str, parent_id: str) -> dict[str, Any]: ...

    def create_file(
        self,
        *,
        name: str,
        parent_id: str,
        mime_type: str | None = None,
        text: str | None = None,
        content_base64: str | None = None,
        local_path: str | None = None,
    ) -> dict[str, Any]: ...

    def rename_file(self, file_id: str, new_name: str) -> dict[str, Any]: ...

    def copy_file(
        self,
        file_id: str,
        *,
        name: str | None = None,
        parent_id: str | None = None,
    ) -> dict[str, Any]: ...

    def move_file(
        self, file_id: str, add_parent: str, remove_parent: str
    ) -> dict[str, Any]: ...

    def set_trashed(self, file_id: str, trashed: bool) -> dict[str, Any]: ...

    def delete_file(self, file_id: str) -> dict[str, Any]: ...

    def list_permissions(
        self,
        file_id_or_name: str,
        *,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]: ...

    def get_permission(self, file_id: str, permission_id: str) -> dict[str, Any]: ...

    def create_permission(
        self,
        file_id: str,
        *,
        permission_type: str,
        role: str,
        email_address: str | None = None,
        domain: str | None = None,
        allow_file_discovery: bool | None = None,
        send_notification_email: bool = True,
        expiration_time: str | None = None,
    ) -> dict[str, Any]: ...

    def update_permission(
        self, file_id: str, permission_id: str, role: str
    ) -> dict[str, Any]: ...

    def delete_permission(self, file_id: str, permission_id: str) -> dict[str, Any]: ...

    def list_shared_drives(
        self, *, page_size: int = 100, page_token: str | None = None
    ) -> dict[str, Any]: ...

    def get_start_page_token(
        self, *, drive_id: str | None = None
    ) -> dict[str, Any]: ...

    def list_changes(
        self,
        page_token: str,
        *,
        drive_id: str | None = None,
        page_size: int = 100,
        include_removed: bool = True,
    ) -> dict[str, Any]: ...

    def find_child_folder(self, parent_id: str, name: str) -> dict[str, Any] | None: ...
