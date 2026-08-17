from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from driveops_mcp.audit import AuditStore
from driveops_mcp.planner import (
    DEFAULT_ARCHIVE_FOLDER,
    DriveOpsPlanner,
    target_folder_name,
)
from driveops_mcp.schemas import GOOGLE_FOLDER_MIME


class FakeDrive:
    def __init__(self) -> None:
        self.folder = {
            "id": "folder_root",
            "name": "Root",
            "mimeType": GOOGLE_FOLDER_MIME,
        }
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
        folder_id = (
            self.folder["id"]
            if folder_id_or_name
            in {self.folder.get("name"), self.folder.get("id"), "My Drive"}
            else folder_id_or_name
        )
        children = [
            item
            for item in list(self.files.values()) + list(self.folders.values())
            if folder_id in item.get("parents", []) and not item.get("trashed", False)
        ]
        return {
            "folder": dict(self.folders.get(folder_id, self.folder)),
            "count": len(children),
            "has_more": False,
            "files": [dict(x) for x in children],
        }

    def find_child_folder(self, parent_id: str, name: str) -> dict | None:
        for folder in self.folders.values():
            if folder["name"] == name and parent_id in folder.get("parents", []):
                return dict(folder)
        return None

    def create_folder(self, name: str, parent_id: str) -> dict:
        folder_id = f"folder_{name}"
        folder = {
            "id": folder_id,
            "name": name,
            "mimeType": GOOGLE_FOLDER_MIME,
            "parents": [parent_id],
        }
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

    def set_trashed(self, file_id: str, trashed: bool) -> dict:
        collection = self.files if file_id in self.files else self.folders
        collection[file_id]["trashed"] = trashed
        return dict(collection[file_id])


class ActionFakeDrive(FakeDrive):
    def __init__(self) -> None:
        super().__init__()
        self.folders["target"] = {
            "id": "target",
            "name": "Target",
            "mimeType": GOOGLE_FOLDER_MIME,
            "parents": ["folder_root"],
        }
        self.permissions = {
            "perm1": {
                "id": "perm1",
                "type": "user",
                "role": "reader",
                "emailAddress": "old@example.com",
            }
        }
        self.next_id = 1

    def resolve_file(self, reference: str) -> dict:
        if reference in self.files:
            return dict(self.files[reference])
        match = next(
            (item for item in self.files.values() if item["name"] == reference), None
        )
        if match:
            return dict(match)
        raise KeyError(reference)

    resolve_file_exact = resolve_file

    def resolve_folder(self, reference: str) -> dict:
        if reference in {"My Drive", "root"}:
            return {"id": "root", "name": "My Drive", "mimeType": GOOGLE_FOLDER_MIME}
        if reference in {"folder_root", "Root"}:
            return dict(self.folder)
        if reference in self.folders:
            return dict(self.folders[reference])
        match = next(
            (item for item in self.folders.values() if item["name"] == reference), None
        )
        if match:
            return dict(match)
        raise KeyError(reference)

    resolve_folder_exact = resolve_folder

    def rename_file(self, file_id: str, new_name: str) -> dict:
        self.files[file_id]["name"] = new_name
        return dict(self.files[file_id])

    def copy_file(
        self, file_id: str, *, name: str | None = None, parent_id: str | None = None
    ) -> dict:
        source = self.files[file_id]
        new_id = f"new{self.next_id}"
        self.next_id += 1
        copy = dict(
            source,
            id=new_id,
            name=name or source["name"],
            parents=[parent_id] if parent_id else list(source["parents"]),
        )
        self.files[new_id] = copy
        return dict(copy)

    def create_file(self, *, name: str, parent_id: str, **kwargs) -> dict:
        new_id = f"new{self.next_id}"
        self.next_id += 1
        file = {
            "id": new_id,
            "name": name,
            "mimeType": kwargs.get("mime_type") or "text/plain",
            "parents": [parent_id],
        }
        self.files[new_id] = file
        return dict(file)

    def set_trashed(self, file_id: str, trashed: bool) -> dict:
        collection = self.files if file_id in self.files else self.folders
        collection[file_id]["trashed"] = trashed
        return dict(collection[file_id])

    def delete_file(self, file_id: str) -> dict:
        del self.files[file_id]
        return {"id": file_id, "deleted": True}

    def get_permission(self, file_id: str, permission_id: str) -> dict:
        return dict(self.permissions[permission_id])

    def create_permission(self, file_id: str, **kwargs) -> dict:
        permission_id = f"perm{len(self.permissions) + 1}"
        permission = {
            "id": permission_id,
            "type": kwargs["permission_type"],
            "role": kwargs["role"],
            "emailAddress": kwargs.get("email_address"),
            "domain": kwargs.get("domain"),
            "allowFileDiscovery": kwargs.get("allow_file_discovery"),
            "expirationTime": kwargs.get("expiration_time"),
        }
        self.permissions[permission_id] = permission
        return dict(permission)

    def update_permission(self, file_id: str, permission_id: str, role: str) -> dict:
        self.permissions[permission_id]["role"] = role
        return dict(self.permissions[permission_id])

    def delete_permission(self, file_id: str, permission_id: str) -> dict:
        self.permissions.pop(permission_id)
        return {"file_id": file_id, "permission_id": permission_id, "deleted": True}

    def list_permissions(self, file_id_or_name: str, **kwargs) -> dict:
        return {"permissions": [dict(item) for item in self.permissions.values()]}


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


def test_plan_preview_and_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = FakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))

    plan = planner.plan_organize_folder(
        folder_id_or_name="folder_root",
        strategy="by_created_month",
    )

    assert plan["status"] == "planned"
    assert plan["summary"] == {
        "files_seen": 2,
        "folders_to_create": 2,
        "files_to_move": 2,
    }
    preview = planner.preview_plan(plan["plan_id"])
    result = planner.apply_plan(
        plan_id=plan["plan_id"], confirmation=preview["confirmation"]
    )

    assert result["status"] == "applied"
    assert drive.files["f1"]["parents"] == ["folder_2026-06"]
    assert drive.files["f2"]["parents"] == ["folder_2026-05"]
    events = planner.audit.list_events(plan_id=plan["plan_id"])
    assert any(e["action"] == "move_file" and e["status"] == "ok" for e in events)


def test_apply_rejects_without_write_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "readonly")
    planner = DriveOpsPlanner(FakeDrive(), store(tmp_path))
    plan = planner.plan_organize_folder(
        folder_id_or_name="folder_root", strategy="by_created_month"
    )

    with pytest.raises(PermissionError):
        planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])


def test_apply_rejects_stale_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = FakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_organize_folder(
        folder_id_or_name="folder_root", strategy="by_created_month"
    )
    drive.files["f1"]["parents"] = ["somewhere_else"]

    with pytest.raises(RuntimeError, match="Stale plan"):
        planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])

    failed = planner.audit.get_plan(plan["plan_id"])
    assert failed["status"] == "partially_applied"


def test_undo_restores_file_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = FakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_organize_folder(
        folder_id_or_name="folder_root", strategy="by_created_month"
    )
    planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])

    result = planner.undo_plan(
        plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"]
    )

    assert result["status"] == "undone"
    assert drive.files["f1"]["parents"] == ["folder_root"]
    assert drive.files["f2"]["parents"] == ["folder_root"]
    assert all(folder["trashed"] is True for folder in drive.folders.values())


def test_concurrent_apply_claims_plan_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")

    class SlowDrive(ActionFakeDrive):
        def __init__(self) -> None:
            super().__init__()
            self.create_calls = 0
            self.lock = threading.Lock()

        def create_folder(self, name: str, parent_id: str) -> dict:
            with self.lock:
                self.create_calls += 1
            time.sleep(0.1)
            return super().create_folder(name, parent_id)

    drive = SlowDrive()
    audit = store(tmp_path)
    planner = DriveOpsPlanner(drive, audit)
    plan = planner.plan_file_actions(
        actions=[{"type": "create_folder", "name": "Reports"}]
    )
    start = threading.Barrier(2)
    results: list[str] = []

    def apply() -> None:
        start.wait()
        try:
            DriveOpsPlanner(drive, store(tmp_path)).apply_plan(
                plan_id=plan["plan_id"], confirmation=plan["confirmation"]
            )
            results.append("applied")
        except ValueError:
            results.append("rejected")

    threads = [threading.Thread(target=apply) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["applied", "rejected"]
    assert drive.create_calls == 1
    assert audit.get_plan(plan["plan_id"])["status"] == "applied"


def test_latest_plan_shortcuts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = FakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_organize_folder(
        folder_id_or_name="folder_root", strategy="by_mime_type"
    )

    preview = planner.preview_plan()
    assert preview["plan_id"] == plan["plan_id"]
    applied = planner.apply_plan(confirmation=preview["confirmation"])
    assert applied["plan_id"] == plan["plan_id"]
    undone = planner.undo_plan(confirmation=preview["undo_confirmation"])
    assert undone["plan_id"] == plan["plan_id"]


def test_general_file_actions_apply_and_undo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = ActionFakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))

    plan = planner.plan_file_actions(
        actions=[
            {"type": "rename_file", "file_id_or_name": "f1", "new_name": "renamed.pdf"},
            {
                "type": "move_file",
                "file_id_or_name": "f1",
                "target_folder_id_or_name": "Target",
            },
            {"type": "trash_file", "file_id_or_name": "f2"},
        ]
    )
    result = planner.apply_plan(
        plan_id=plan["plan_id"], confirmation=plan["confirmation"]
    )

    assert result["status"] == "applied"
    assert drive.files["f1"]["name"] == "renamed.pdf"
    assert drive.files["f1"]["parents"] == ["target"]
    assert drive.files["f2"]["trashed"] is True

    undone = planner.undo_plan(
        plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"]
    )
    assert undone["status"] == "undone"
    assert drive.files["f1"]["name"] == "alpha-report.pdf"
    assert drive.files["f1"]["parents"] == ["folder_root"]
    assert drive.files["f2"]["trashed"] is False
    events = planner.audit.list_events(plan_id=plan["plan_id"])
    undo_move = next(event for event in events if event["action"] == "undo_move_file")
    assert undo_move["before"] == {"parents": ["target"]}


def test_upload_action_is_preserved_redacted_and_undoable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = ActionFakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    source = tmp_path / "private-upload.txt"
    source.write_text("upload body", encoding="utf-8")

    plan = planner.plan_file_actions(
        actions=[
            {
                "type": "upload_file",
                "local_path": str(source),
                "parent_id_or_name": "My Drive",
            }
        ]
    )

    assert plan["summary"]["action_counts"] == {"upload_file": 1}
    assert plan["steps"][0]["type"] == "upload_file"
    assert plan["steps"][0]["local_path"] == "<redacted local path>"
    planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])
    applied = planner.audit.get_plan(plan["plan_id"])
    created_file_id = applied["steps"][0]["created_file_id"]
    assert drive.files[created_file_id]["name"] == source.name

    planner.undo_plan(plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"])
    assert drive.files[created_file_id]["trashed"] is True


def test_file_action_plan_rejects_multiple_content_sources(tmp_path: Path) -> None:
    drive = ActionFakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    source = tmp_path / "upload.txt"
    source.write_text("upload body", encoding="utf-8")

    with pytest.raises(ValueError, match="only one"):
        planner.plan_file_actions(
            actions=[
                {
                    "type": "upload_file",
                    "local_path": str(source),
                    "text": "conflicting inline body",
                }
            ]
        )


def test_mutation_planning_requires_exact_resolution(tmp_path: Path) -> None:
    class NoExactMatchDrive(ActionFakeDrive):
        def resolve_file(self, reference: str) -> dict:
            return dict(self.files["f1"])

        def resolve_file_exact(self, reference: str) -> dict:
            raise KeyError("no exact match")

    planner = DriveOpsPlanner(NoExactMatchDrive(), store(tmp_path))

    with pytest.raises(KeyError, match="no exact match"):
        planner.plan_file_actions(
            actions=[
                {
                    "type": "rename_file",
                    "file_id_or_name": "fuzzy report",
                    "new_name": "renamed.pdf",
                }
            ]
        )


def test_general_create_copy_share_and_redacted_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = ActionFakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_file_actions(
        actions=[
            {"type": "create_file", "name": "hello.txt", "text": "private body"},
            {"type": "copy_file", "file_id_or_name": "f1", "new_name": "copy.pdf"},
            {
                "type": "share_file",
                "file_id_or_name": "f1",
                "email_address": "person@example.com",
                "role": "reader",
            },
        ]
    )
    assert plan["steps"][0]["text"].startswith("<redacted text:")

    planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])
    assert any(item["name"] == "hello.txt" for item in drive.files.values())
    assert any(item["name"] == "copy.pdf" for item in drive.files.values())
    assert any(
        item.get("emailAddress") == "person@example.com"
        for item in drive.permissions.values()
    )

    planner.undo_plan(plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"])
    created = [
        item
        for item in drive.files.values()
        if item["name"] in {"hello.txt", "copy.pdf"}
    ]
    assert all(item["trashed"] is True for item in created)
    assert not any(
        item.get("emailAddress") == "person@example.com"
        for item in drive.permissions.values()
    )


def test_permission_changes_are_stale_checked_and_expiration_is_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = ActionFakeDrive()
    drive.permissions["perm1"]["expirationTime"] = "2026-09-01T00:00:00Z"
    planner = DriveOpsPlanner(drive, store(tmp_path))
    stale = planner.plan_file_actions(
        actions=[
            {
                "type": "update_permission",
                "file_id_or_name": "f1",
                "permission_id": "perm1",
                "role": "writer",
            }
        ]
    )
    drive.permissions["perm1"]["role"] = "commenter"

    with pytest.raises(RuntimeError, match="permission perm1 changed"):
        planner.apply_plan(plan_id=stale["plan_id"], confirmation=stale["confirmation"])

    drive.permissions["perm1"]["role"] = "reader"
    removal = planner.plan_file_actions(
        actions=[
            {
                "type": "remove_permission",
                "file_id_or_name": "f1",
                "permission_id": "perm1",
            }
        ]
    )
    planner.apply_plan(plan_id=removal["plan_id"], confirmation=removal["confirmation"])
    planner.undo_plan(
        plan_id=removal["plan_id"], confirmation=removal["undo_confirmation"]
    )

    restored = next(iter(drive.permissions.values()))
    assert restored["expirationTime"] == "2026-09-01T00:00:00Z"


def test_ownership_transfer_is_not_supported(tmp_path: Path) -> None:
    planner = DriveOpsPlanner(ActionFakeDrive(), store(tmp_path))

    with pytest.raises(ValueError, match="unsupported permission role"):
        planner.plan_file_actions(
            actions=[
                {
                    "type": "share_file",
                    "file_id_or_name": "f1",
                    "email_address": "new-owner@example.com",
                    "role": "owner",
                }
            ]
        )


def test_permanent_delete_plan_is_marked_irreversible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = ActionFakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_file_actions(
        actions=[{"type": "delete_file", "file_id_or_name": "f1"}]
    )

    assert plan["summary"]["irreversible_actions"] == 1
    planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])
    with pytest.raises(ValueError, match="cannot be undone"):
        planner.undo_plan(
            plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"]
        )


def test_partially_applied_plan_is_persisted_but_stale_undo_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")

    class ExternallyChangedDrive(ActionFakeDrive):
        mutate_after_snapshot = False

        def rename_file(self, file_id: str, new_name: str) -> dict:
            result = super().rename_file(file_id, new_name)
            if new_name == "first.pdf":
                self.mutate_after_snapshot = True
            return result

        def get_file(self, file_id: str, fields: str | None = None) -> dict:
            result = super().get_file(file_id, fields)
            if self.mutate_after_snapshot and result.get("name") == "first.pdf":
                self.mutate_after_snapshot = False
                self.files[file_id]["modifiedTime"] = "2026-07-01T00:00:00"
            return result

    drive = ExternallyChangedDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_file_actions(
        actions=[
            {"type": "rename_file", "file_id_or_name": "f1", "new_name": "first.pdf"},
            {"type": "rename_file", "file_id_or_name": "f1", "new_name": "second.pdf"},
        ]
    )

    with pytest.raises(RuntimeError, match="Stale plan"):
        planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])
    assert planner.audit.get_plan(plan["plan_id"])["status"] == "partially_applied"
    assert drive.files["f1"]["name"] == "first.pdf"

    with pytest.raises(RuntimeError, match="changed after apply"):
        planner.undo_plan(
            plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"]
        )


def test_permanent_delete_must_be_isolated(tmp_path: Path) -> None:
    planner = DriveOpsPlanner(ActionFakeDrive(), store(tmp_path))

    with pytest.raises(ValueError, match="own plan"):
        planner.plan_file_actions(
            actions=[
                {"type": "delete_file", "file_id_or_name": "f1"},
                {"type": "trash_file", "file_id_or_name": "f2"},
            ]
        )


def test_apply_preserves_previewed_action_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")

    class OrderedDrive(ActionFakeDrive):
        calls: list[str]

        def __init__(self) -> None:
            super().__init__()
            self.calls = []

        def create_file(self, **kwargs) -> dict:
            self.calls.append("create_file")
            return super().create_file(**kwargs)

        def create_folder(self, name: str, parent_id: str) -> dict:
            self.calls.append("create_folder")
            return super().create_folder(name, parent_id)

    drive = OrderedDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_file_actions(
        actions=[
            {"type": "create_file", "name": "first.txt"},
            {"type": "create_folder", "name": "Second"},
        ]
    )

    planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])

    assert drive.calls == ["create_file", "create_folder"]


def test_apply_refuses_changed_file_for_nonrename_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = ActionFakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_file_actions(
        actions=[{"type": "trash_file", "file_id_or_name": "f1"}]
    )
    drive.files["f1"]["modifiedTime"] = "2026-08-01T00:00:00"

    with pytest.raises(RuntimeError, match="Stale plan"):
        planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])

    assert not drive.files["f1"].get("trashed", False)


def test_undo_refuses_to_trash_created_file_after_user_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = ActionFakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_file_actions(
        actions=[{"type": "create_file", "name": "draft.txt"}]
    )
    planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])
    applied = planner.audit.get_plan(plan["plan_id"])
    file_id = applied["steps"][0]["created_file_id"]
    drive.files[file_id]["name"] = "user-renamed.txt"

    with pytest.raises(RuntimeError, match="created file"):
        planner.undo_plan(
            plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"]
        )

    assert not drive.files[file_id].get("trashed", False)


def test_undo_refuses_to_trash_created_folder_with_new_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = ActionFakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_file_actions(
        actions=[{"type": "create_folder", "name": "Reports"}]
    )
    planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])
    applied = planner.audit.get_plan(plan["plan_id"])
    folder_id = applied["steps"][0]["created_folder_id"]
    drive.create_file(name="user-note.txt", parent_id=folder_id)

    with pytest.raises(RuntimeError, match="no longer empty"):
        planner.undo_plan(
            plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"]
        )

    assert not drive.folders[folder_id].get("trashed", False)


def test_partial_undo_is_persisted_and_retry_skips_completed_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIVEOPS_SCOPE_PROFILE", "write")
    drive = ActionFakeDrive()
    planner = DriveOpsPlanner(drive, store(tmp_path))
    plan = planner.plan_file_actions(
        actions=[
            {"type": "rename_file", "file_id_or_name": "f1", "new_name": "first.pdf"},
            {"type": "rename_file", "file_id_or_name": "f2", "new_name": "second.txt"},
        ]
    )
    planner.apply_plan(plan_id=plan["plan_id"], confirmation=plan["confirmation"])

    rename_file = drive.rename_file

    def fail_first_file(file_id: str, new_name: str) -> dict:
        if file_id == "f1":
            raise RuntimeError("simulated undo failure")
        return rename_file(file_id, new_name)

    drive.rename_file = fail_first_file  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="simulated undo failure"):
        planner.undo_plan(
            plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"]
        )

    partial = planner.audit.get_plan(plan["plan_id"])
    assert partial["status"] == "partially_undone"
    assert partial["steps"][1]["undo_status"] == "undone"
    assert drive.files["f2"]["name"] == "beta-notes.txt"

    retried_files: list[str] = []

    def track_retry(file_id: str, new_name: str) -> dict:
        retried_files.append(file_id)
        return rename_file(file_id, new_name)

    drive.rename_file = track_retry  # type: ignore[method-assign]
    result = planner.undo_plan(
        plan_id=plan["plan_id"], confirmation=plan["undo_confirmation"]
    )

    assert result["status"] == "undone"
    assert retried_files == ["f1"]
    assert drive.files["f1"]["name"] == "alpha-report.pdf"


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

    plan = planner.plan_organize_folder(
        folder_id_or_name="folder_root", strategy="by_name_prefix"
    )
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
        planner.plan_organize_folder(
            folder_id_or_name="folder_root", strategy="by_created_month"
        )


def test_hygiene_report_flags_common_drive_clutter(tmp_path: Path) -> None:
    drive = FakeDrive()
    drive.folder = {"id": "root", "name": "My Drive", "mimeType": GOOGLE_FOLDER_MIME}
    drive.files["f1"]["parents"] = ["root"]
    drive.files["f2"]["parents"] = ["root"]
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

    report = planner.hygiene_report(
        folder_id_or_name="My Drive", stale_days=30, large_mb=100
    )

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
    result = planner.apply_plan(
        plan_id=plan["plan_id"], confirmation=preview["confirmation"]
    )

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
