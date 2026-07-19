"""Google Drive permissions, shared-drive, and change-feed operations."""

from __future__ import annotations

from typing import Any


class GoogleAccessMixin:
    """Access-related operations mixed into the Google Drive backend."""

    drive: Any

    def resolve_file(self, file_id_or_name: str) -> dict[str, Any]:
        raise NotImplementedError

    def list_permissions(
        self,
        file_id_or_name: str,
        *,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        file = self.resolve_file(file_id_or_name)
        params: dict[str, Any] = {
            "fileId": file["id"],
            "pageSize": max(1, min(int(page_size), 100)),
            "fields": "nextPageToken,permissions(id,type,role,emailAddress,domain,displayName,expirationTime,deleted,allowFileDiscovery,permissionDetails)",
            "supportsAllDrives": True,
        }
        if page_token:
            params["pageToken"] = page_token
        response = self.drive.permissions().list(**params).execute()
        return {
            "file": {"id": file["id"], "name": file.get("name")},
            "count": len(response.get("permissions", [])),
            "permissions": response.get("permissions", []),
            "next_page_token": response.get("nextPageToken"),
            "has_more": bool(response.get("nextPageToken")),
        }

    def get_permission(self, file_id: str, permission_id: str) -> dict[str, Any]:
        return (
            self.drive.permissions()
            .get(
                fileId=file_id,
                permissionId=permission_id,
                fields="id,type,role,emailAddress,domain,displayName,expirationTime,deleted,allowFileDiscovery",
                supportsAllDrives=True,
            )
            .execute()
        )

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
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"type": permission_type, "role": role}
        if email_address:
            body["emailAddress"] = email_address
        if domain:
            body["domain"] = domain
        if allow_file_discovery is not None:
            body["allowFileDiscovery"] = allow_file_discovery
        return (
            self.drive.permissions()
            .create(
                fileId=file_id,
                body=body,
                sendNotificationEmail=send_notification_email
                if permission_type in {"user", "group"}
                else False,
                fields="id,type,role,emailAddress,domain,displayName,expirationTime,allowFileDiscovery",
                supportsAllDrives=True,
            )
            .execute()
        )

    def update_permission(
        self, file_id: str, permission_id: str, role: str
    ) -> dict[str, Any]:
        return (
            self.drive.permissions()
            .update(
                fileId=file_id,
                permissionId=permission_id,
                body={"role": role},
                fields="id,type,role,emailAddress,domain,displayName,expirationTime,allowFileDiscovery",
                supportsAllDrives=True,
            )
            .execute()
        )

    def delete_permission(self, file_id: str, permission_id: str) -> dict[str, Any]:
        self.drive.permissions().delete(
            fileId=file_id, permissionId=permission_id, supportsAllDrives=True
        ).execute()
        return {"file_id": file_id, "permission_id": permission_id, "deleted": True}

    def list_shared_drives(
        self, *, page_size: int = 100, page_token: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "pageSize": max(1, min(int(page_size), 100)),
            "fields": "nextPageToken,drives(id,name,createdTime,hidden,restrictions,capabilities)",
        }
        if page_token:
            params["pageToken"] = page_token
        response = self.drive.drives().list(**params).execute()
        return {
            "count": len(response.get("drives", [])),
            "drives": response.get("drives", []),
            "next_page_token": response.get("nextPageToken"),
            "has_more": bool(response.get("nextPageToken")),
        }

    def get_start_page_token(self, *, drive_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"supportsAllDrives": True}
        if drive_id:
            params["driveId"] = drive_id
        response = self.drive.changes().getStartPageToken(**params).execute()
        return {"drive_id": drive_id, "start_page_token": response["startPageToken"]}

    def list_changes(
        self,
        page_token: str,
        *,
        drive_id: str | None = None,
        page_size: int = 100,
        include_removed: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "pageToken": page_token,
            "pageSize": max(1, min(int(page_size), 1000)),
            "includeRemoved": include_removed,
            "supportsAllDrives": True,
            "fields": "nextPageToken,newStartPageToken,changes(fileId,removed,time,changeType,driveId,file(id,name,mimeType,parents,trashed,modifiedTime,webViewLink))",
        }
        if drive_id:
            params.update({"driveId": drive_id, "includeItemsFromAllDrives": True})
        response = self.drive.changes().list(**params).execute()
        return {
            "drive_id": drive_id,
            "count": len(response.get("changes", [])),
            "changes": response.get("changes", []),
            "next_page_token": response.get("nextPageToken"),
            "new_start_page_token": response.get("newStartPageToken"),
            "has_more": bool(response.get("nextPageToken")),
        }
