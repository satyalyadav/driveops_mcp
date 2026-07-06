from __future__ import annotations

from pathlib import Path

import pytest

from driveops_mcp.audit import AuditStore
from driveops_mcp.planner import DEFAULT_ARCHIVE_FOLDER, DriveOpsPlanner, target_folder_name
from driveops_mcp.schemas import GOOGLE_FOLDER_MIME


class FakeDrive:
    def __init__(self) -> None:
        self.folder = {"id": "folder_root", "name": "Root", "mimeType": GOOGLE_FOLDER_MIME}
        self.files = {
            "f1": {
                "id": "f1",
                "name": "alpha-report.pdf",
                "mimeType": "application/pdf",
                "createdTime": "2026-06-01T00:00:00",
                "modifiedTime": "2026-06-15T00:00:00",
                "parents": ["folder_root"],
            },
            "f2": {
                "id": "f2",
                "name": "beta-notes.txt",
                "mimeType": "text/plain",
                "createdTime": "2026-05-01T00:00:00",
                "modifiedTime": "2026-06-20T00:00:00",
                "parents": ["folder_root"],
            },
        }
        self.folders: dict[str, dict] = {}

    def list_folder(self, folder_id_or_name: str, page_size: int = 1000) -> dict:
        children = list(self.files.values()) + list(self.folders.values())
        return {"folder": self.folder, "count": len(children), "has_more": False, "files": [dict(x) for x in children]}

    def find_child_folder(self, parent_id: str, name: str) -> dict | None:
        for folder in self.folders.values():
            if folder["name"] == name and parent_id in folder.get("parents", []):
                return dict(folder)
        return None

    def create_folder(self, name: str, parent_id: str) -> dict:
        folder_id = f"folder_{name}"
        folder = {"id": folder_id, "name": name, "mimeType": GOOGLE_FOLDER_MIME, "parents": [parent_id]}
        self.folders[folder_id] = folder
        return dict(folder)

    def get_file(self, file_id: str, fields: str | None = None) -> dict:
        if file_id in self.files:
            return dict(self.files[file_id])
        if file_id in self.folders:
            return dict(self.folders[file_id])
        raise KeyError(file_id)

    def move_file(self, file_id: str, add_parent: str, remove_parent: str) -> dict:
        file = self.files[file_id]
        parents = [p for p in file["parents"] if p != remove_parent]
        if add_parent not in parents:
            parents.append(add_parent)
        file["parents"] = parents
        return dict(file)


def store(tmp_path: Path) -> AuditStore:
    return AuditStore(tmp_path / "driveops.db")


def test_target_folder_name_strategies() -> None:
    file = {
        "name": "Alpha report.pdf",
        "mimeType": "application/pdf",
        "createdTime": "2026-06-01T00:00:00",
        "modifiedTime": "2026-07-01T00:00:00",
    }
    assert target_folder_name(file, "by_created_month") == "2026-06"
    assert target_folder_name(file, "by_modified_month") == "2026-07"
    assert target_folder_name(file, "by_mime_type") == "documents"
    assert target_folder_name(file, "by_name_prefix") == "alpha"


def test_plan_preview_and_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = FakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))

    plan = planner.plan_organize_folder(
        folder_id_or_name="folder_root",
        strategy="by_created_month",
    )

    assert plan["status"] == "planned"
    assert plan["summary"] == {"files_seen": 2, "folders_to_create": 2, "files_to_move": 2}
    preview = planner.preview_plan(plan["plan_id"])
    result = planner.apply_plan(plan_id=plan["plan_id"], confirmation=preview["confirmation"])

    assert result["status"] == "applied"
    assert drive.files["f1"]["parents"] == ["folder_2026-06"]
    assert drive.files["f2"]["parents"] == ["folder_2026-05"]
    events = planner.audit.list_events(plan_id=plan["plan_id"])
    assert any(e["action"] == "move_file" and e["status"] == "ok" for e in events)


def test_apply_rejects_without_write_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "readonly")
    planner = DriveOpsPlanner(FakeDrive(), store(tmp_path))
    plan = planner.plan_organize_folder(folder_id_or_name="folder_root", strategy="by_created_month")

    with pytest.raises(PermissionError):
        planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])


def test_apply_rejects_stale_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = FakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_organize_folder(folder_id_or_name="folder_root", strategy="by_created_month")
    drive.files["f1"]["parents"] = ["somewhere_else"]

    with pytest.raises(RuntimeError, match="Stale plan"):
        planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])

    failed = planner.audit.get_plan(plan["plan_id"])
    assert failed["status"] == "failed"


def test_undo_restores_file_parents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = FakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_organize_folder(folder_id_or_name="folder_root", strategy="by_created_month")
    planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])

    result = planner.undo_plan(plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"])

    assert result["status"] == "undone"
    assert drive.files["f1"]["parents"] == ["folder_root"]
    assert drive.files["f2"]["parents"] == ["folder_root"]


def test_latest_plan_shortcuts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = FakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_organize_folder(folder_id_or_name="folder_root", strategy="by_mime_type")

    preview = planner.preview_plan()
    assert preview["plan_id"] == plan["plan_id"]
    applied = planner.apply_plan(confirmation=preview["confirmation"])
    assert applied["plan_id"] == plan["plan_id"]
    undone = planner.undo_plan(confirmation=preview["undo_confirmation"])
    assert undone["plan_id"] == plan["plan_id"]


def test_plan_response_is_compact_by_default(tmp_path: Path) -> None:
    drive = FakeDrive()
    for index in range(25):
        file_id = f"extra_{index}"
        drive.files[file_id] = {
            "id": file_id,
            "name": f"extra-{index}.txt",
            "mimeType": "text/plain",
            "createdTime": "2026-06-01T00:00:00",
            "modifiedTime": "2026-06-01T00:00:00",
            "parents": ["folder_root"],
        }
    planner = DriveOpsPlanner(drive, store(tmp_path))

    plan = planner.plan_organize_folder(folder_id_or_name="folder_root", strategy="by_name_prefix")
    preview = planner.preview_plan(plan["plan_id"], max_steps=5)
    full = planner.preview_plan(plan["plan_id"], detail="full")

    assert plan["steps_truncated"] is True
    assert preview["steps_total"] > preview["steps_returned"]
    assert preview["steps_returned"] == 5
    assert full["steps_truncated"] is False
    assert full["steps_returned"] == full["steps_total"]
    assert preview["step_groups"]


def test_plan_refuses_incomplete_folder_listing(tmp_path: Path) -> None:
    class LargeFakeDrive(FakeDrive):
        def list_folder(self, folder_id_or_name: str, page_size: int = 1000) -> dict:
            result = super().list_folder(folder_id_or_name, page_size)
            result["has_more"] = True
            return result

    planner = DriveOpsPlanner(LargeFakeDrive(), store(tmp_path))

    with pytest.raises(ValueError, match="more than 1000 items"):
        planner.plan_organize_folder(folder_id_or_name="folder_root", strategy="by_created_month")


def test_hygiene_report_flags_common_drive_clutter(tmp_path: Path) -> None:
    drive = FakeDrive()
    drive.folder = {"id": "root", "name": "My Drive", "mimeType": GOOGLE_FOLDER_MIME}
    drive.files.update(
        {
            "dup_old": {
                "id": "dup_old",
                "name": "Budget.xlsx",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "createdTime": "2025-01-01T00:00:00+00:00",
                "modifiedTime": "2025-01-01T00:00:00+00:00",
                "parents": ["root"],
            },
            "dup_new": {
                "id": "dup_new",
                "name": "Budget.xlsx",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "createdTime": "2026-01-01T00:00:00+00:00",
                "modifiedTime": "2026-02-01T00:00:00+00:00",
                "parents": ["root"],
            },
            "video": {
                "id": "video",
                "name": "vacation.mov",
                "mimeType": "video/quicktime",
                "createdTime": "2026-01-01T00:00:00+00:00",
                "modifiedTime": "2026-02-01T00:00:00+00:00",
                "size": str(250 * 1024 * 1024),
                "parents": ["root"],
            },
            "tax": {
                "id": "tax",
                "name": "2025 tax return.pdf",
                "mimeType": "application/pdf",
                "createdTime": "2026-01-01T00:00:00+00:00",
                "modifiedTime": "2026-02-01T00:00:00+00:00",
                "parents": ["root"],
            },
        }
    )
    drive.folders["old_folder"] = {
        "id": "old_folder",
        "name": "Old Project",
        "mimeType": GOOGLE_FOLDER_MIME,
        "modifiedTime": "2020-01-01T00:00:00+00:00",
        "parents": ["root"],
    }
    planner = DriveOpsPlanner(drive, store(tmp_path))

    report = planner.hygiene_report(folder_id_or_name="My Drive", stale_days=30, large_mb=100)

    assert report["status"] == "ok"
    assert report["summary"]["loose_root_files"] == 6
    assert report["summary"]["duplicate_name_groups"] == 1
    assert report["summary"]["stale_folders"] == 1
    assert report["summary"]["large_binaries"] == 1
    assert report["summary"]["sensitive_looking_docs"] == 1
    assert report["summary"]["unmanaged_media"] == 1
    assert {plan["tool"] for plan in report["suggested_plans"]} == {
        "driveops.plan_duplicate_cleanup",
        "driveops.plan_organize_folder",
    }


def test_duplicate_cleanup_plan_archives_older_duplicates_and_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = FakeDrive()
    drive.files.update(
        {
            "resume_base": {
                "id": "resume_base",
                "name": "Resume.pdf",
                "mimeType": "application/pdf",
                "createdTime": "2026-01-01T00:00:00+00:00",
                "modifiedTime": "2026-01-10T00:00:00+00:00",
                "parents": ["folder_root"],
            },
            "resume_latest": {
                "id": "resume_latest",
                "name": "Resume latest.pdf",
                "mimeType": "application/pdf",
                "createdTime": "2026-01-01T00:00:00+00:00",
                "modifiedTime": "2026-03-10T00:00:00+00:00",
                "parents": ["folder_root"],
            },
            "notes_old": {
                "id": "notes_old",
                "name": "Notes.txt",
                "mimeType": "text/plain",
                "createdTime": "2026-01-01T00:00:00+00:00",
                "modifiedTime": "2026-01-10T00:00:00+00:00",
                "parents": ["folder_root"],
            },
            "notes_new": {
                "id": "notes_new",
                "name": "Notes.txt",
                "mimeType": "text/plain",
                "createdTime": "2026-01-01T00:00:00+00:00",
                "modifiedTime": "2026-04-10T00:00:00+00:00",
                "parents": ["folder_root"],
            },
        }
    )
    planner = DriveOpsPlanner(drive, store(tmp_path))

    plan = planner.plan_duplicate_cleanup(folder_id_or_name="Root")
    preview = planner.preview_plan(plan["plan_id"])
    result = planner.apply_plan(plan_id=plan["plan_id"], confirmation=preview["confirmation"])

    assert plan["strategy"] == "duplicate_cleanup"
    assert plan["summary"]["files_to_archive"] == 2
    assert plan["summary"]["files_to_delete"] == 0
    assert plan["steps"][0]["folder_name"] == DEFAULT_ARCHIVE_FOLDER
    assert result["status"] == "applied"
    archive_id = f"folder_{DEFAULT_ARCHIVE_FOLDER}"
    assert drive.files["resume_base"]["parents"] == [archive_id]
    assert drive.files["resume_latest"]["parents"] == ["folder_root"]
    assert drive.files["notes_old"]["parents"] == [archive_id]
    assert drive.files["notes_new"]["parents"] == ["folder_root"]


def test_duplicate_cleanup_no_changes_does_not_store_empty_plan(tmp_path: Path) -> None:
    planner = DriveOpsPlanner(FakeDrive(), store(tmp_path))

    result = planner.plan_duplicate_cleanup(folder_id_or_name="Root")

    assert result["status"] == "no_changes"
    assert result["plan_id"] is None
    assert result["summary"]["files_to_archive"] == 0
    with pytest.raises(KeyError, match="No DriveOps plan"):
        planner.preview_plan()
