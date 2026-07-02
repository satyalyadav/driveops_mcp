from __future__ import annotations

from driveops_mcp.google_drive import GoogleDriveClient


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
                        ]
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
    class Files:
        def get(self, **kwargs):
            class Call:
                def execute(self):
                    return {"id": "abc123456789", "name": "Doc", "mimeType": "text/plain"}

            return Call()

    class Service:
        def files(self):
            return Files()

    client = GoogleDriveClient(drive_service=Service())
    try:
        client.resolve_folder("abc123456789")
    except Exception as exc:
        assert "not a Google Drive folder" in str(exc)
    else:
        raise AssertionError("Expected resolve_folder to reject a non-folder ID")


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
                            {"id": "f1", "name": "One", "mimeType": "text/plain", "parents": ["root"]},
                            {"id": "f2", "name": "Two", "mimeType": "text/plain", "parents": ["root"]},
                        ],
                    }

            return Call()

    class Service:
        def files(self):
            return Files()

    result = GoogleDriveClient(drive_service=Service()).search_files(query="*", page_size=2)

    assert len(calls) == 1
    assert calls[0]["pageSize"] == 2
    assert result["count"] == 2
    assert result["has_more"] is True
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
                            {"id": "f1", "name": "One", "mimeType": "text/plain", "parents": ["root"]},
                        ],
                    }

            return Call()

    class Service:
        def files(self):
            return Files()

    result = GoogleDriveClient(drive_service=Service()).list_folder("My Drive", page_size=1)

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
                        "files": [
                            {
                                "id": "dup_1",
                                "name": "Notes",
                                "mimeType": "text/plain",
                                "parents": ["root"],
                                "modifiedTime": "2026-01-01T00:00:00",
                            },
                            {
                                "id": "dup_2",
                                "name": "Notes",
                                "mimeType": "text/plain",
                                "parents": ["root"],
                                "modifiedTime": "2026-02-01T00:00:00",
                            },
                        ]
                    }

            return Call()

    class Service:
        def files(self):
            return Files()

    result = GoogleDriveClient(drive_service=Service()).read_file("Notes")

    assert result["status"] == "ambiguous"
    assert result["contentType"] == "ambiguous"
    assert result["text"] is None
    assert len(result["matches"]) == 2
    assert "Multiple files matched" in result["message"]
