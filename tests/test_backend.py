from driveops_mcp.backend import DriveBackend
from driveops_mcp.google_drive import GoogleDriveClient


def test_google_client_implements_drive_backend_contract():
    client = GoogleDriveClient(drive_service=object())

    assert isinstance(client, DriveBackend)
