"""Google Drive API adapter used by DriveOps tools."""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from .auth import get_credentials
from .schemas import (
    GOOGLE_DOC_MIME,
    GOOGLE_FOLDER_MIME,
    GOOGLE_SHEET_MIME,
    GOOGLE_SLIDE_MIME,
    normalize_file,
)


class DriveOpsError(RuntimeError):
    pass


class AmbiguousFileError(DriveOpsError):
    def __init__(self, file_name: str, matches: list[dict[str, Any]], has_more: bool = False) -> None:
        super().__init__(f"Multiple files named '{file_name}' found.")
        self.file_name = file_name
        self.matches = matches
        self.has_more = has_more


class GoogleDriveClient:
    def __init__(self, drive_service: Any | None = None, sheets_service: Any | None = None) -> None:
        if drive_service is None:
            creds = get_credentials()
            drive_service = build("drive", "v3", credentials=creds)
            sheets_service = build("sheets", "v4", credentials=creds)
        self.drive = drive_service
        self.sheets = sheets_service

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("'", "\\'")

    @staticmethod
    def _looks_like_id(value: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{10,}", value or ""))

    def _execute_files_page(self, *, max_items: int | None = None, **params: Any) -> tuple[list[dict[str, Any]], bool]:
        items: list[dict[str, Any]] = []
        page_token = params.pop("pageToken", None)
        has_more = False
        while True:
            call_params = dict(params)
            if max_items is not None:
                remaining = max_items - len(items)
                if remaining <= 0:
                    has_more = bool(page_token)
                    break
                requested = int(call_params.get("pageSize", remaining))
                call_params["pageSize"] = max(1, min(requested, remaining))
            if page_token:
                call_params["pageToken"] = page_token
            resp = self.drive.files().list(**call_params).execute()
            items.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if max_items is not None and len(items) >= max_items:
                has_more = bool(page_token)
                break
            if not page_token:
                break
        return items[:max_items] if max_items is not None else items, has_more

    def _execute_files_list(self, **params: Any) -> list[dict[str, Any]]:
        files, _ = self._execute_files_page(**params)
        return files

    def get_file(self, file_id: str, fields: str | None = None) -> dict[str, Any]:
        fields = fields or "id,name,mimeType,createdTime,modifiedTime,size,parents,webViewLink,webContentLink"
        return self.drive.files().get(
            fileId=file_id,
            fields=fields,
            supportsAllDrives=True,
        ).execute()

    def resolve_file(self, file_id_or_name: str) -> dict[str, Any]:
        value = file_id_or_name.strip()
        if not value:
            raise DriveOpsError("file_id_or_name is required.")
        if self._looks_like_id(value):
            try:
                return self.get_file(value)
            except Exception:
                pass

        exact, has_more = self._exact_file_matches(value)
        if len(exact) > 1:
            raise AmbiguousFileError(value, self.enrich_files(exact), has_more)
        if exact:
            return exact[0]
        search = self.search_files(query=value, page_size=1)
        files = search.get("files", [])
        if files:
            return files[0]
        raise DriveOpsError(f"File '{file_id_or_name}' not found.")

    def _exact_file_matches(self, value: str, max_matches: int = 6) -> tuple[list[dict[str, Any]], bool]:
        safe = self._escape(value)
        return self._execute_files_page(
            max_items=max_matches,
            q=f"name = '{safe}' and trashed = false",
            pageSize=max_matches,
            fields="files(id,name,mimeType,createdTime,modifiedTime,size,parents,webViewLink,webContentLink)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
        )

    def resolve_folder(self, folder_id_or_name: str) -> dict[str, Any]:
        value = folder_id_or_name.strip()
        if not value:
            raise DriveOpsError("folder_id_or_name is required.")
        if value.lower() in {"root", "my drive", "drive", "/"}:
            return {"id": "root", "name": "My Drive", "mimeType": GOOGLE_FOLDER_MIME, "parents": []}
        if self._looks_like_id(value):
            folder = self.get_file(value, fields="id,name,mimeType,parents")
            if folder.get("mimeType") != GOOGLE_FOLDER_MIME:
                raise DriveOpsError(f"{value} is not a Google Drive folder.")
            return folder

        cleaned = value
        if cleaned.lower().startswith("my ") and cleaned.lower() != "my drive":
            cleaned = cleaned[3:].strip()
        if cleaned.lower().endswith(" folder"):
            cleaned = cleaned[:-7].strip()
        safe = self._escape(cleaned)
        candidates = self._execute_files_list(
            q=f"name contains '{safe}' and mimeType = '{GOOGLE_FOLDER_MIME}' and trashed = false",
            pageSize=10,
            fields="files(id,name,mimeType,parents)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
        )
        for item in candidates:
            if item.get("name", "").lower() == cleaned.lower():
                return item
        if candidates:
            return candidates[0]
        raise DriveOpsError(f"Folder '{folder_id_or_name}' not found.")

    def folder_path(self, parent_id: str, cache: dict[str, str] | None = None) -> str:
        cache = cache if cache is not None else {}
        if not parent_id or parent_id == "root":
            return "My Drive"
        if parent_id in cache:
            return cache[parent_id]
        try:
            folder = self.get_file(parent_id, fields="id,name,parents")
            parents = folder.get("parents", [])
            name = folder.get("name", "Unknown")
            if not parents or parents[0] == "root":
                path = f"My Drive > {name}" if name != "My Drive" else "My Drive"
            else:
                path = f"{self.folder_path(parents[0], cache)} > {name}"
            cache[parent_id] = path
            return path
        except Exception:
            return "Unknown"

    def enrich_files(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cache: dict[str, str] = {}
        enriched = []
        for item in files:
            parents = item.get("parents", [])
            paths = [self.folder_path(parent, cache) for parent in parents] or ["My Drive"]
            item = dict(item)
            item["folderPaths"] = paths
            item["folderPath"] = paths[0]
            if not item.get("webViewLink") and item.get("id"):
                item["webViewLink"] = f"https://drive.google.com/file/d/{item['id']}/view"
            enriched.append(normalize_file(item))
        return enriched

    def search_files(
        self,
        *,
        query: str,
        folder_id: str | None = None,
        mime_types: list[str] | None = None,
        page_size: int = 10,
    ) -> dict[str, Any]:
        query = (query or "*").strip()
        page_size = max(1, min(int(page_size), 100))
        clauses = ["trashed = false"]
        if folder_id:
            folder = self.resolve_folder(folder_id)
            clauses.append(f"'{folder['id']}' in parents")
        if mime_types:
            mime_clause = " or ".join(f"mimeType = '{self._escape(m)}'" for m in mime_types)
            clauses.append(f"({mime_clause})")

        raw_query = any(op in query for op in [" contains ", "=", " in parents", "fullText"])
        if query != "*":
            if raw_query:
                clauses.insert(0, f"({query})")
            else:
                tokens = [t for t in re.split(r"\s+", query) if t]
                token_clauses = []
                for token in tokens:
                    safe = self._escape(token)
                    token_clauses.append(f"(name contains '{safe}' or fullText contains '{safe}')")
                if token_clauses:
                    clauses.insert(0, "(" + " and ".join(token_clauses) + ")")

        files, has_more = self._execute_files_page(
            max_items=page_size,
            q=" and ".join(clauses),
            pageSize=page_size,
            fields="nextPageToken, files(id,name,mimeType,createdTime,modifiedTime,size,parents,webViewLink,webContentLink)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
        )
        return {
            "query": query,
            "count": len(files),
            "has_more": has_more,
            "files": self.enrich_files(files),
        }

    def list_folder(self, folder_id_or_name: str, page_size: int = 100) -> dict[str, Any]:
        folder = self.resolve_folder(folder_id_or_name)
        page_size = max(1, min(int(page_size), 1000))
        files, has_more = self._execute_files_page(
            max_items=page_size,
            q=f"'{folder['id']}' in parents and trashed = false",
            pageSize=page_size,
            fields="nextPageToken, files(id,name,mimeType,createdTime,modifiedTime,size,parents,webViewLink,webContentLink)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
        )
        return {"folder": folder, "count": len(files), "has_more": has_more, "files": self.enrich_files(files)}

    def get_changes(self, folder_id_or_name: str, since: str, page_size: int = 100) -> dict[str, Any]:
        folder = self.resolve_folder(folder_id_or_name)
        try:
            date_part = since.split("T", 1)[0]
            since_dt = datetime.fromisoformat(date_part)
        except ValueError as exc:
            raise DriveOpsError("since must be an ISO date like YYYY-MM-DD.") from exc
        since_str = since_dt.strftime("%Y-%m-%dT00:00:00")
        page_size = max(1, min(int(page_size), 1000))
        files, has_more = self._execute_files_page(
            max_items=page_size,
            q=f"'{folder['id']}' in parents and modifiedTime >= '{since_str}' and trashed = false",
            pageSize=page_size,
            fields="nextPageToken, files(id,name,mimeType,createdTime,modifiedTime,size,parents,webViewLink,webContentLink)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
            orderBy="modifiedTime desc",
        )
        enriched = self.enrich_files(files)
        for item in enriched:
            item["changeType"] = "new" if (item.get("createdTime") or "") >= since_str else "edited"
        return {"folder": folder, "since": since, "count": len(enriched), "has_more": has_more, "changedFiles": enriched}

    def read_file(self, file_id_or_name: str, export_format: str | None = None, max_text_bytes: int = 200_000) -> dict[str, Any]:
        try:
            resolved = self.resolve_file(file_id_or_name)
        except AmbiguousFileError as exc:
            return {
                "status": "ambiguous",
                "contentType": "ambiguous",
                "text": None,
                "name": exc.file_name,
                "matches": exc.matches,
                "has_more": exc.has_more,
                "message": "Multiple files matched this name. Ask the user to choose one by path, modified time, or file ID.",
            }
        file_id = resolved["id"]
        meta = self.get_file(file_id, fields="id,name,mimeType,size,webViewLink,webContentLink")
        mime = meta.get("mimeType", "")
        export_mime = export_format
        if mime == GOOGLE_DOC_MIME:
            export_mime = export_mime or "text/plain"
        elif mime == GOOGLE_SHEET_MIME:
            export_mime = export_mime or "text/csv"
        elif mime == GOOGLE_SLIDE_MIME:
            export_mime = export_mime or "text/plain"

        if mime.startswith("application/vnd.google-apps"):
            request = self.drive.files().export(fileId=file_id, mimeType=export_mime or "application/pdf")
            data = self._download_request(request, max_text_bytes + 1)
            return self._content_response(meta, data, export_mime or "application/octet-stream", max_text_bytes)

        if mime.startswith("text/") or mime in {"application/json", "application/xml"}:
            request = self.drive.files().get_media(fileId=file_id)
            data = self._download_request(request, max_text_bytes + 1)
            return self._content_response(meta, data, mime, max_text_bytes)

        return {
            "id": file_id,
            "name": meta.get("name"),
            "mimeType": mime,
            "contentType": "download_hint",
            "text": None,
            "truncated": False,
            "webViewLink": meta.get("webViewLink"),
            "webContentLink": meta.get("webContentLink"),
            "message": "Binary or large non-text content was not loaded into model context.",
        }

    @staticmethod
    def _download_request(request: Any, max_bytes: int) -> bytes:
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done and fh.tell() <= max_bytes:
            _, done = downloader.next_chunk()
        return fh.getvalue()

    @staticmethod
    def _content_response(meta: dict[str, Any], data: bytes, content_type: str, max_text_bytes: int) -> dict[str, Any]:
        truncated = len(data) > max_text_bytes
        raw = data[:max_text_bytes]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        return {
            "id": meta.get("id"),
            "name": meta.get("name"),
            "mimeType": meta.get("mimeType"),
            "contentType": content_type,
            "text": text,
            "truncated": truncated,
            "webViewLink": meta.get("webViewLink"),
            "webContentLink": meta.get("webContentLink"),
        }

    def create_folder(self, name: str, parent_id: str) -> dict[str, Any]:
        body = {"name": name, "mimeType": GOOGLE_FOLDER_MIME, "parents": [parent_id]}
        return self.drive.files().create(
            body=body,
            fields="id,name,mimeType,parents",
            supportsAllDrives=True,
        ).execute()

    def move_file(self, file_id: str, add_parent: str, remove_parent: str) -> dict[str, Any]:
        return self.drive.files().update(
            fileId=file_id,
            addParents=add_parent,
            removeParents=remove_parent,
            fields="id,name,mimeType,parents,modifiedTime",
            supportsAllDrives=True,
        ).execute()

    def find_child_folder(self, parent_id: str, name: str) -> dict[str, Any] | None:
        safe = self._escape(name)
        files, _ = self._execute_files_page(
            max_items=1,
            q=f"'{parent_id}' in parents and name = '{safe}' and mimeType = '{GOOGLE_FOLDER_MIME}' and trashed = false",
            pageSize=1,
            fields="files(id,name,mimeType,parents)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
        )
        return files[0] if files else None
