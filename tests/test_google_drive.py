from __future__ import annotations

import base64
import io
import zipfile

import pytest

from driveops_mcp import content
from driveops_mcp.google_drive import (
    AmbiguousFolderError,
    DriveOpsError,
    GoogleDriveClient,
)


def test_binary_read_returns_download_hint() -> None:
    class Files:
        def get(self, **kwargs):
            class Call:
                def execute(self):
                    return {
                        "id": "binary_file_12345",
                        "name": "image.png",
                        "mimeType": "image/png",
                        "webViewLink": "https://drive.google.com/file/d/bin/view",
                    }

            return Call()

    class Service:
        def files(self):
            return Files()

    client = GoogleDriveClient(drive_service=Service())
    result = client.read_file("binary_file_12345")
    assert result["contentType"] == "download_hint"
    assert result["text"] is None


def test_read_file_resolves_name() -> None:
    class Files:
        def list(self, **kwargs):
            class Call:
                def execute(self):
                    return {
                        "files": [
                            {
                                "id": "text_file_12345",
                                "name": "Notes",
                                "mimeType": "text/plain",
                                "parents": ["root"],
                            }
                        ],
                    }

            return Call()

        def get(self, **kwargs):
            class Call:
                def execute(self):
                    return {
                        "id": "text_file_12345",
                        "name": "Notes",
                        "mimeType": "text/plain",
                    }

            return Call()

        def get_media(self, **kwargs):
            class Request:
                pass

            return Request()

    class Service:
        def files(self):
            return Files()

    client = GoogleDriveClient(drive_service=Service())
    client._download_request = lambda request, max_bytes: b"hello from notes"  # type: ignore[method-assign]
    result = client.read_file("Notes")
    assert result["id"] == "text_file_12345"
    assert result["text"] == "hello from notes"


def test_resolve_folder_rejects_non_folder_id() -> None:
    folder_id = "abc1234567890123456789"

    class Files:
        def get(self, **kwargs):
            class Call:
                def execute(self):
                    return {
                        "id": folder_id,
                        "name": "Doc",
                        "mimeType": "text/plain",
                    }

            return Call()

    class Service:
        def files(self):
            return Files()

    client = GoogleDriveClient(drive_service=Service())
    try:
        client.resolve_folder(folder_id)
    except Exception as exc:
        assert "not a Google Drive folder" in str(exc)
    else:
        raise AssertionError("Expected resolve_folder to reject a non-folder ID")


def test_resolve_folder_rejects_ambiguous_names() -> None:
    class Call:
        def execute(self):
            return {
                "files": [
                    {
                        "id": "folder_one_12345",
                        "name": "Applications",
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": ["root"],
                    },
                    {
                        "id": "folder_two_12345",
                        "name": "Applications",
                        "mimeType": "application/vnd.google-apps.folder",
                        "parents": ["root"],
                    },
                ]
            }

    class Files:
        def list(self, **kwargs):
            return Call()

    class Service:
        def files(self):
            return Files()

    client = GoogleDriveClient(drive_service=Service())

    with pytest.raises(AmbiguousFolderError) as error:
        client.resolve_folder("Applications")

    assert len(error.value.matches) == 2
    assert "folder_one_12345" in str(error.value)
    assert "Use a folder ID" in str(error.value)


def test_search_files_respects_page_size_and_reports_more() -> None:
    calls = []

    class Files:
        def list(self, **kwargs):
            calls.append(kwargs)

            class Call:
                def execute(self):
                    return {
                        "nextPageToken": "next",
                        "files": [
                            {
                                "id": "f1",
                                "name": "One",
                                "mimeType": "text/plain",
                                "parents": ["root"],
                            },
                            {
                                "id": "f2",
                                "name": "Two",
                                "mimeType": "text/plain",
                                "parents": ["root"],
                            },
                        ],
                    }

            return Call()

    class Service:
        def files(self):
            return Files()

    result = GoogleDriveClient(drive_service=Service()).search_files(
        query="*", page_size=2
    )

    assert len(calls) == 1
    assert calls[0]["pageSize"] == 2
    assert result["count"] == 2
    assert result["has_more"] is True
    assert result["next_page_token"] == "next"
    assert [item["name"] for item in result["files"]] == ["One", "Two"]


def test_list_folder_resolves_my_drive_to_root_and_caps_results() -> None:
    calls = []

    class Files:
        def list(self, **kwargs):
            calls.append(kwargs)

            class Call:
                def execute(self):
                    return {
                        "nextPageToken": "next",
                        "files": [
                            {
                                "id": "f1",
                                "name": "One",
                                "mimeType": "text/plain",
                                "parents": ["root"],
                            },
                        ],
                    }

            return Call()

    class Service:
        def files(self):
            return Files()

    result = GoogleDriveClient(drive_service=Service()).list_folder(
        "My Drive", page_size=1
    )

    assert len(calls) == 1
    assert calls[0]["q"] == "'root' in parents and trashed = false"
    assert calls[0]["pageSize"] == 1
    assert result["folder"]["id"] == "root"
    assert result["folder"]["name"] == "My Drive"
    assert result["count"] == 1
    assert result["has_more"] is True


def test_read_file_returns_ambiguity_for_duplicate_exact_names() -> None:
    class Files:
        def list(self, **kwargs):
            class Call:
                def execute(self):
                    return {
                        "nextPageToken": "more-exact-matches",
                        "files": [
                            {
                                "id": f"dup_{index}",
                                "name": "Notes",
                                "mimeType": "text/plain",
                                "parents": ["root"],
                                "modifiedTime": f"2026-01-0{index + 1}T00:00:00",
                            }
                            for index in range(6)
                        ],
                    }

            return Call()

    class Service:
        def files(self):
            return Files()

    result = GoogleDriveClient(drive_service=Service()).read_file("Notes")

    assert result["status"] == "ambiguous"
    assert result["contentType"] == "ambiguous"
    assert result["text"] is None
    assert len(result["matches"]) == 6
    assert result["has_more"] is True
    assert isinstance(result["has_more"], bool)
    assert "Multiple files matched" in result["message"]


def test_download_file_returns_real_base64_bytes() -> None:
    class Files:
        def get(self, **kwargs):
            class Call:
                def execute(self):
                    return {
                        "id": "binary_file_12345",
                        "name": "photo.png",
                        "mimeType": "image/png",
                    }

            return Call()

        def get_media(self, **kwargs):
            return object()

    class Service:
        def files(self):
            return Files()

    client = GoogleDriveClient(drive_service=Service())
    client._download_request = lambda request, max_bytes: b"actual-bytes"  # type: ignore[method-assign]
    result = client.download_file("binary_file_12345")

    assert base64.b64decode(result["content_base64"]) == b"actual-bytes"
    assert result["size"] == 12


def test_download_request_bounds_transport_chunk_size(monkeypatch) -> None:
    observed: dict[str, int] = {}

    class Downloader:
        def __init__(self, output, request, chunksize):
            observed["chunksize"] = chunksize
            self.output = output

        def next_chunk(self):
            self.output.write(b"x" * observed["chunksize"])
            return None, True

    monkeypatch.setattr(content, "MediaIoBaseDownload", Downloader)

    result = content.download_request(object(), 257)

    assert observed["chunksize"] == 257
    assert len(result) == 257


def test_download_file_refuses_to_overwrite(tmp_path) -> None:
    target = tmp_path / "existing.bin"
    target.write_bytes(b"keep")

    class Files:
        def get(self, **kwargs):
            class Call:
                def execute(self):
                    return {
                        "id": "binary_file_12345",
                        "name": "existing.bin",
                        "mimeType": "application/octet-stream",
                    }

            return Call()

        def get_media(self, **kwargs):
            return object()

    class Service:
        def files(self):
            return Files()

    client = GoogleDriveClient(drive_service=Service())
    client._download_request = lambda request, max_bytes: b"replace"  # type: ignore[method-assign]
    with pytest.raises(DriveOpsError, match="already exists"):
        client.download_file("binary_file_12345", output_path=str(target))
    assert target.read_bytes() == b"keep"


def test_permissions_shared_drives_and_changes_are_paginated() -> None:
    class Call:
        def __init__(self, response):
            self.response = response

        def execute(self):
            return self.response

    class Files:
        def get(self, **kwargs):
            return Call(
                {"id": "file_123456789", "name": "Doc", "mimeType": "text/plain"}
            )

    class Permissions:
        def list(self, **kwargs):
            return Call(
                {
                    "permissions": [{"id": "p1", "type": "user", "role": "reader"}],
                    "nextPageToken": "p2",
                }
            )

    class Drives:
        def list(self, **kwargs):
            return Call({"drives": [{"id": "d1", "name": "Team"}]})

    class Changes:
        def getStartPageToken(self, **kwargs):
            return Call({"startPageToken": "start"})

        def list(self, **kwargs):
            return Call(
                {
                    "changes": [{"fileId": "f1", "removed": True}],
                    "newStartPageToken": "new",
                }
            )

    class Service:
        def files(self):
            return Files()

        def permissions(self):
            return Permissions()

        def drives(self):
            return Drives()

        def changes(self):
            return Changes()

    client = GoogleDriveClient(drive_service=Service())
    permissions = client.list_permissions("file_123456789")
    drives = client.list_shared_drives()
    token = client.get_start_page_token()
    changes = client.list_changes("start")

    assert permissions["next_page_token"] == "p2"
    assert drives["drives"][0]["name"] == "Team"
    assert token["start_page_token"] == "start"
    assert changes["changes"][0]["removed"] is True
    assert changes["new_start_page_token"] == "new"


def test_extract_docx_and_safely_unpack_zip(tmp_path) -> None:
    from docx import Document

    document_buffer = io.BytesIO()
    document = Document()
    document.add_paragraph("Hello from DOCX")
    document.save(document_buffer)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("folder/note.txt", "archive text")

    payloads = {
        "docx_file_12345": (
            "notes.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            document_buffer.getvalue(),
        ),
        "zip_file_123456": ("bundle.zip", "application/zip", zip_buffer.getvalue()),
    }

    class Call:
        def __init__(self, response):
            self.response = response

        def execute(self):
            return self.response

    class Files:
        def get(self, **kwargs):
            name, mime, _ = payloads[kwargs["fileId"]]
            return Call({"id": kwargs["fileId"], "name": name, "mimeType": mime})

        def get_media(self, **kwargs):
            return kwargs["fileId"]

    class Service:
        def files(self):
            return Files()

    client = GoogleDriveClient(drive_service=Service())
    client._download_request = lambda request, max_bytes: payloads[request][2]  # type: ignore[method-assign]

    docx_result = client.extract_file("docx_file_12345")
    zip_result = client.extract_file(
        "zip_file_123456", output_dir=str(tmp_path / "unpacked")
    )

    assert "Hello from DOCX" in docx_result["text"]
    assert zip_result["entry_count"] == 1
    assert (tmp_path / "unpacked" / "folder" / "note.txt").read_text() == "archive text"


def test_zip_extraction_blocks_path_traversal(tmp_path) -> None:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("../escape.txt", "bad")

    class Call:
        def execute(self):
            return {
                "id": "zip_file_123456",
                "name": "bad.zip",
                "mimeType": "application/zip",
            }

    class Files:
        def get(self, **kwargs):
            return Call()

        def get_media(self, **kwargs):
            return object()

    class Service:
        def files(self):
            return Files()

    client = GoogleDriveClient(drive_service=Service())
    client._download_request = lambda request, max_bytes: zip_buffer.getvalue()  # type: ignore[method-assign]
    with pytest.raises(DriveOpsError, match="unsafe ZIP path"):
        client.extract_file("zip_file_123456", output_dir=str(tmp_path / "unpacked"))
    assert not (tmp_path / "escape.txt").exists()


def test_file_and_permission_mutations_call_drive_api() -> None:
    calls = []

    class Call:
        def __init__(self, response=None):
            self.response = response or {}

        def execute(self):
            return self.response

    class Files:
        def create(self, **kwargs):
            calls.append(("create", kwargs))
            return Call({"id": "new", "name": kwargs["body"]["name"]})

        def update(self, **kwargs):
            calls.append(("update", kwargs))
            return Call({"id": kwargs["fileId"], **kwargs.get("body", {})})

        def copy(self, **kwargs):
            calls.append(("copy", kwargs))
            return Call({"id": "copy", **kwargs["body"]})

        def delete(self, **kwargs):
            calls.append(("delete", kwargs))
            return Call()

    class Permissions:
        def create(self, **kwargs):
            calls.append(("permission_create", kwargs))
            return Call({"id": "p2", **kwargs["body"]})

        def update(self, **kwargs):
            calls.append(("permission_update", kwargs))
            return Call({"id": kwargs["permissionId"], **kwargs["body"]})

        def delete(self, **kwargs):
            calls.append(("permission_delete", kwargs))
            return Call()

    class Service:
        def files(self):
            return Files()

        def permissions(self):
            return Permissions()

    client = GoogleDriveClient(drive_service=Service())
    client.create_folder("Folder", "root")
    client.create_file(name="note.txt", parent_id="root", text="hello")
    client.rename_file("f1", "renamed.txt")
    client.copy_file("f1", name="copy.txt", parent_id="root")
    client.move_file("f1", "target", "root")
    client.set_trashed("f1", True)
    client.delete_file("f1")
    client.create_permission(
        "f2", permission_type="user", role="reader", email_address="person@example.com"
    )
    client.update_permission("f2", "p2", "writer")
    client.delete_permission("f2", "p2")

    names = [name for name, _ in calls]
    assert names == [
        "create",
        "create",
        "update",
        "copy",
        "update",
        "update",
        "delete",
        "permission_create",
        "permission_update",
        "permission_delete",
    ]
    assert calls[4][1]["addParents"] == "target"
    assert calls[5][1]["body"] == {"trashed": True}
    assert calls[7][1]["body"]["emailAddress"] == "person@example.com"
