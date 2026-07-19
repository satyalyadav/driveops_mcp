"""Google Drive file resolution, transfer, and mutation operations."""

from __future__ import annotations

import base64
import io
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

from .content import (
    content_response,
    default_export_format,
    download_name,
    download_request,
    extract_content,
)
from .errors import AmbiguousFileError, AmbiguousFolderError, DriveOpsError
from .schemas import (
    GOOGLE_DOC_MIME,
    GOOGLE_FOLDER_MIME,
    GOOGLE_SHEET_MIME,
    GOOGLE_SLIDE_MIME,
    normalize_file,
)


class GoogleFilesMixin:
    drive: Any
    sheets: Any

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("'", "\\'")

    @staticmethod
    def _looks_like_id(value: str) -> bool:
        return bool(
            re.fullmatch(r"[A-Za-z0-9_-]{10,}", value or "")
            and (len(value) >= 20 or "_" in value or "-" in value)
        )

    def _execute_files_page(
        self, *, max_items: int | None = None, **params: Any
    ) -> tuple[list[dict[str, Any]], str | None]:
        items: list[dict[str, Any]] = []
        page_token = params.pop("pageToken", None)
        while True:
            call_params = dict(params)
            if max_items is not None:
                remaining = max_items - len(items)
                if remaining <= 0:
                    break
                requested = int(call_params.get("pageSize", remaining))
                call_params["pageSize"] = max(1, min(requested, remaining))
            if page_token:
                call_params["pageToken"] = page_token
            resp = self.drive.files().list(**call_params).execute()
            items.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if max_items is not None and len(items) >= max_items:
                break
            if not page_token:
                break
        return items[:max_items] if max_items is not None else items, page_token

    def _execute_files_list(self, **params: Any) -> list[dict[str, Any]]:
        files, _ = self._execute_files_page(**params)
        return files

    def get_file(self, file_id: str, fields: str | None = None) -> dict[str, Any]:
        fields = (
            fields
            or "id,name,mimeType,createdTime,modifiedTime,size,parents,webViewLink,webContentLink"
        )
        return (
            self.drive.files()
            .get(
                fileId=file_id,
                fields=fields,
                supportsAllDrives=True,
            )
            .execute()
        )

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

    def _exact_file_matches(
        self, value: str, max_matches: int = 6
    ) -> tuple[list[dict[str, Any]], bool]:
        safe = self._escape(value)
        files, next_page_token = self._execute_files_page(
            max_items=max_matches,
            q=f"name = '{safe}' and trashed = false",
            pageSize=max_matches,
            fields="nextPageToken, files(id,name,mimeType,createdTime,modifiedTime,size,parents,webViewLink,webContentLink)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
        )
        return files, bool(next_page_token)

    def resolve_folder(self, folder_id_or_name: str) -> dict[str, Any]:
        value = folder_id_or_name.strip()
        if not value:
            raise DriveOpsError("folder_id_or_name is required.")
        if value.lower() in {"root", "my drive", "drive", "/"}:
            return {
                "id": "root",
                "name": "My Drive",
                "mimeType": GOOGLE_FOLDER_MIME,
                "parents": [],
            }
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
        candidates, next_page_token = self._execute_files_page(
            max_items=6,
            q=f"name contains '{safe}' and mimeType = '{GOOGLE_FOLDER_MIME}' and trashed = false",
            pageSize=6,
            fields="nextPageToken,files(id,name,mimeType,parents,modifiedTime,webViewLink)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
        )
        exact = [
            item
            for item in candidates
            if item.get("name", "").casefold() == cleaned.casefold()
        ]
        matches = exact or candidates
        if len(matches) > 1 or (matches and next_page_token):
            raise AmbiguousFolderError(
                folder_id_or_name,
                self.enrich_files(matches),
                bool(next_page_token),
            )
        if matches:
            return matches[0]
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
            paths = [self.folder_path(parent, cache) for parent in parents] or [
                "My Drive"
            ]
            item = dict(item)
            item["folderPaths"] = paths
            item["folderPath"] = paths[0]
            if not item.get("webViewLink") and item.get("id"):
                item["webViewLink"] = (
                    f"https://drive.google.com/file/d/{item['id']}/view"
                )
            enriched.append(normalize_file(item))
        return enriched

    def search_files(
        self,
        *,
        query: str,
        folder_id: str | None = None,
        mime_types: list[str] | None = None,
        page_size: int = 10,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        query = (query or "*").strip()
        page_size = max(1, min(int(page_size), 100))
        clauses = ["trashed = false"]
        if folder_id:
            folder = self.resolve_folder(folder_id)
            clauses.append(f"'{folder['id']}' in parents")
        if mime_types:
            mime_clause = " or ".join(
                f"mimeType = '{self._escape(m)}'" for m in mime_types
            )
            clauses.append(f"({mime_clause})")

        raw_query = any(
            op in query for op in [" contains ", "=", " in parents", "fullText"]
        )
        if query != "*":
            if raw_query:
                clauses.insert(0, f"({query})")
            else:
                tokens = [t for t in re.split(r"\s+", query) if t]
                token_clauses = []
                for token in tokens:
                    safe = self._escape(token)
                    token_clauses.append(
                        f"(name contains '{safe}' or fullText contains '{safe}')"
                    )
                if token_clauses:
                    clauses.insert(0, "(" + " and ".join(token_clauses) + ")")

        files, next_page_token = self._execute_files_page(
            max_items=page_size,
            q=" and ".join(clauses),
            pageSize=page_size,
            fields="nextPageToken, files(id,name,mimeType,createdTime,modifiedTime,size,parents,webViewLink,webContentLink)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
            pageToken=page_token,
        )
        return {
            "query": query,
            "count": len(files),
            "has_more": bool(next_page_token),
            "next_page_token": next_page_token,
            "files": self.enrich_files(files),
        }

    def list_folder(
        self,
        folder_id_or_name: str,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        folder = self.resolve_folder(folder_id_or_name)
        page_size = max(1, min(int(page_size), 1000))
        files, next_page_token = self._execute_files_page(
            max_items=page_size,
            q=f"'{folder['id']}' in parents and trashed = false",
            pageSize=page_size,
            fields="nextPageToken, files(id,name,mimeType,createdTime,modifiedTime,size,parents,webViewLink,webContentLink)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
            pageToken=page_token,
        )
        return {
            "folder": folder,
            "count": len(files),
            "has_more": bool(next_page_token),
            "next_page_token": next_page_token,
            "files": self.enrich_files(files),
        }

    def get_changes(
        self,
        folder_id_or_name: str,
        since: str,
        page_size: int = 100,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        folder = self.resolve_folder(folder_id_or_name)
        try:
            date_part = since.split("T", 1)[0]
            since_dt = datetime.fromisoformat(date_part)
        except ValueError as exc:
            raise DriveOpsError("since must be an ISO date like YYYY-MM-DD.") from exc
        since_str = since_dt.strftime("%Y-%m-%dT00:00:00")
        page_size = max(1, min(int(page_size), 1000))
        files, next_page_token = self._execute_files_page(
            max_items=page_size,
            q=f"'{folder['id']}' in parents and modifiedTime >= '{since_str}' and trashed = false",
            pageSize=page_size,
            fields="nextPageToken, files(id,name,mimeType,createdTime,modifiedTime,size,parents,webViewLink,webContentLink)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            corpora="allDrives",
            orderBy="modifiedTime desc",
            pageToken=page_token,
        )
        enriched = self.enrich_files(files)
        for item in enriched:
            item["changeType"] = (
                "new" if (item.get("createdTime") or "") >= since_str else "edited"
            )
        return {
            "folder": folder,
            "since": since,
            "count": len(enriched),
            "has_more": bool(next_page_token),
            "next_page_token": next_page_token,
            "changedFiles": enriched,
        }

    def read_file(
        self,
        file_id_or_name: str,
        export_format: str | None = None,
        max_text_bytes: int = 200_000,
    ) -> dict[str, Any]:
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
        meta = self.get_file(
            file_id, fields="id,name,mimeType,size,webViewLink,webContentLink"
        )
        mime = meta.get("mimeType", "")
        export_mime = export_format
        if mime == GOOGLE_DOC_MIME:
            export_mime = export_mime or "text/plain"
        elif mime == GOOGLE_SHEET_MIME:
            export_mime = export_mime or "text/csv"
        elif mime == GOOGLE_SLIDE_MIME:
            export_mime = export_mime or "text/plain"

        if mime.startswith("application/vnd.google-apps"):
            request = self.drive.files().export(
                fileId=file_id, mimeType=export_mime or "application/pdf"
            )
            data = self._download_request(request, max_text_bytes + 1)
            return self._content_response(
                meta, data, export_mime or "application/octet-stream", max_text_bytes
            )

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

    def download_file(
        self,
        file_id_or_name: str,
        *,
        export_format: str | None = None,
        output_path: str | None = None,
        max_bytes: int = 25_000_000,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Download a blob or export a Workspace file as actual bytes.

        With ``output_path`` the bytes are written on the MCP server host. Without
        it, reasonably sized content is returned as base64 for remote MCP clients.
        """

        meta = self.resolve_file(file_id_or_name)
        meta = self.get_file(
            meta["id"], fields="id,name,mimeType,size,webViewLink,webContentLink"
        )
        mime = meta.get("mimeType", "")
        content_type = mime
        if mime.startswith("application/vnd.google-apps"):
            export_format = export_format or self._default_export_format(mime)
            request = self.drive.files().export(
                fileId=meta["id"], mimeType=export_format
            )
            content_type = export_format
        else:
            request = self.drive.files().get_media(
                fileId=meta["id"], supportsAllDrives=True
            )

        max_bytes = max(1, min(int(max_bytes), 100_000_000))
        data = self._download_request(request, max_bytes + 1)
        if len(data) > max_bytes:
            raise DriveOpsError(
                f"Download exceeds the {max_bytes}-byte safety limit. Increase max_bytes up to 100 MB."
            )

        filename = self._download_name(meta.get("name") or meta["id"], content_type)
        result = {
            "id": meta["id"],
            "name": meta.get("name"),
            "filename": filename,
            "mimeType": mime,
            "contentType": content_type,
            "size": len(data),
            "webViewLink": meta.get("webViewLink"),
        }
        if output_path:
            destination = Path(output_path).expanduser()
            if destination.exists() and destination.is_dir():
                destination = destination / filename
            if destination.exists() and not overwrite:
                raise DriveOpsError(
                    f"Download destination already exists: {destination}. Set overwrite=true to replace it."
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            result["saved_path"] = str(destination.resolve())
        else:
            result["content_base64"] = base64.b64encode(data).decode("ascii")
        return result

    def extract_file(
        self,
        file_id_or_name: str,
        *,
        output_dir: str | None = None,
        max_bytes: int = 50_000_000,
        max_text_chars: int = 500_000,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Extract readable text or safely unpack a ZIP archive."""

        resolved = self.resolve_file(file_id_or_name)
        meta = self.get_file(resolved["id"], fields="id,name,mimeType,size,webViewLink")
        mime = meta.get("mimeType", "")
        export_format = None
        if mime == GOOGLE_DOC_MIME:
            export_format = "text/plain"
        elif mime == GOOGLE_SHEET_MIME:
            export_format = "text/csv"
        elif mime == GOOGLE_SLIDE_MIME:
            export_format = "text/plain"

        if mime.startswith("application/vnd.google-apps"):
            if not export_format:
                raise DriveOpsError(f"Text extraction is not supported for {mime}.")
            request = self.drive.files().export(
                fileId=meta["id"], mimeType=export_format
            )
        else:
            request = self.drive.files().get_media(
                fileId=meta["id"], supportsAllDrives=True
            )
        max_bytes = max(1, min(int(max_bytes), 100_000_000))
        data = self._download_request(request, max_bytes + 1)
        if len(data) > max_bytes:
            raise DriveOpsError(
                f"File exceeds the {max_bytes}-byte extraction safety limit."
            )

        return extract_content(
            meta,
            data,
            exported=bool(export_format),
            output_dir=output_dir,
            max_text_chars=max_text_chars,
            overwrite=overwrite,
        )

    # Kept as attributes so callers and tests can replace transfer behavior.
    _default_export_format = staticmethod(default_export_format)
    _download_name = staticmethod(download_name)
    _download_request = staticmethod(download_request)
    _content_response = staticmethod(content_response)

    def create_folder(self, name: str, parent_id: str) -> dict[str, Any]:
        body = {"name": name, "mimeType": GOOGLE_FOLDER_MIME, "parents": [parent_id]}
        return (
            self.drive.files()
            .create(
                body=body,
                fields="id,name,mimeType,parents",
                supportsAllDrives=True,
            )
            .execute()
        )

    def create_file(
        self,
        *,
        name: str,
        parent_id: str,
        mime_type: str | None = None,
        text: str | None = None,
        content_base64: str | None = None,
        local_path: str | None = None,
    ) -> dict[str, Any]:
        sources = sum(value is not None for value in (text, content_base64, local_path))
        if sources > 1:
            raise DriveOpsError(
                "Provide only one of text, content_base64, or local_path."
            )
        body = {"name": name, "parents": [parent_id]}
        media = None
        if local_path is not None:
            path = Path(local_path).expanduser()
            if not path.is_file():
                raise DriveOpsError(f"Upload source is not a file: {path}")
            mime_type = (
                mime_type
                or mimetypes.guess_type(path.name)[0]
                or "application/octet-stream"
            )
            media = MediaFileUpload(str(path), mimetype=mime_type, resumable=True)
        elif content_base64 is not None:
            try:
                raw = base64.b64decode(content_base64, validate=True)
            except ValueError as exc:
                raise DriveOpsError("content_base64 is not valid base64.") from exc
            mime_type = mime_type or "application/octet-stream"
            media = MediaIoBaseUpload(
                io.BytesIO(raw), mimetype=mime_type, resumable=True
            )
        elif text is not None:
            mime_type = mime_type or "text/plain"
            media = MediaIoBaseUpload(
                io.BytesIO(text.encode("utf-8")), mimetype=mime_type, resumable=True
            )
        elif mime_type:
            body["mimeType"] = mime_type

        return (
            self.drive.files()
            .create(
                body=body,
                media_body=media,
                fields="id,name,mimeType,parents,createdTime,modifiedTime,size,webViewLink,webContentLink",
                supportsAllDrives=True,
            )
            .execute()
        )

    def rename_file(self, file_id: str, new_name: str) -> dict[str, Any]:
        return (
            self.drive.files()
            .update(
                fileId=file_id,
                body={"name": new_name},
                fields="id,name,mimeType,parents,modifiedTime,webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )

    def copy_file(
        self, file_id: str, *, name: str | None = None, parent_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        if parent_id:
            body["parents"] = [parent_id]
        return (
            self.drive.files()
            .copy(
                fileId=file_id,
                body=body,
                fields="id,name,mimeType,parents,createdTime,modifiedTime,size,webViewLink,webContentLink",
                supportsAllDrives=True,
            )
            .execute()
        )

    def move_file(
        self, file_id: str, add_parent: str, remove_parent: str
    ) -> dict[str, Any]:
        return (
            self.drive.files()
            .update(
                fileId=file_id,
                addParents=add_parent,
                removeParents=remove_parent,
                fields="id,name,mimeType,parents,modifiedTime",
                supportsAllDrives=True,
            )
            .execute()
        )

    def set_trashed(self, file_id: str, trashed: bool) -> dict[str, Any]:
        return (
            self.drive.files()
            .update(
                fileId=file_id,
                body={"trashed": bool(trashed)},
                fields="id,name,mimeType,parents,trashed,modifiedTime",
                supportsAllDrives=True,
            )
            .execute()
        )

    def delete_file(self, file_id: str) -> dict[str, Any]:
        self.drive.files().delete(fileId=file_id, supportsAllDrives=True).execute()
        return {"id": file_id, "deleted": True}

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
