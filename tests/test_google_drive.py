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
