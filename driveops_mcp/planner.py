"""Safe DriveOps plan generation, application, and undo logic."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .audit import AuditStore
from .auth import require_write_profile
from .google_drive import GoogleDriveClient
from .schemas import (
    GOOGLE_FOLDER_MIME,
    OrganizationStrategy,
    confirmation_for,
    now_iso,
    undo_confirmation_for,
)


MAX_PLAN_ITEMS = 1000
DEFAULT_ARCHIVE_FOLDER = "DriveOps Review - Duplicates"
SENSITIVE_NAME_RE = re.compile(
    r"\b("
    r"1099|bank|credential|credentials|insurance|license|medical|passport|"
    r"password|paystub|ssn|statement|tax|visa|w2|w-2"
    r")\b",
    re.IGNORECASE,
)
VERSION_SUFFIX_RE = re.compile(
    r"(?:"
    r"\s*[-_ ]+\s*(?:copy|draft|final|latest|new|old|updated|version)\s*\d*"
    r"|\s*\(\d+\)"
    r"|\s+v\d+"
    r")+$",
    re.IGNORECASE,
)


def target_folder_name(file: dict[str, Any], strategy: OrganizationStrategy) -> str:
    if strategy == "by_created_month":
        return (file.get("createdTime") or "unknown")[:7] or "unknown"
    if strategy == "by_modified_month":
        return (file.get("modifiedTime") or "unknown")[:7] or "unknown"
    if strategy == "by_mime_type":
        mime = (file.get("mimeType") or "").lower()
        if mime == GOOGLE_FOLDER_MIME:
            return "folders"
        if "spreadsheet" in mime:
            return "spreadsheets"
        if "presentation" in mime:
            return "presentations"
        if "document" in mime or mime.endswith("pdf"):
            return "documents"
        if mime.startswith("image/"):
            return "images"
        if mime.startswith("video/"):
            return "videos"
        return "other"
    if strategy == "by_name_prefix":
        name = file.get("name") or "untitled"
        prefix = re.split(r"[-_ .]", name, maxsplit=1)[0].strip().lower()
        return prefix[:40] or "untitled"
    raise ValueError(f"Unsupported organization strategy: {strategy}")


def _safe_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_drive_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _file_sort_time(file: dict[str, Any]) -> str:
    return file.get("modifiedTime") or file.get("createdTime") or ""


def _extension(name: str) -> str:
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1].lower()


def _version_group_key(name: str) -> str:
    base = name.rsplit(".", 1)[0] if "." in name else name
    base = base.lower().strip()
    previous = None
    while previous != base:
        previous = base
        base = VERSION_SUFFIX_RE.sub("", base).strip()
    base = re.sub(r"[\s_.-]+", " ", base).strip()
    ext = _extension(name)
    return f"{base}.{ext}" if ext else base


def _compact_file(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "mimeType": item.get("mimeType"),
        "modifiedTime": item.get("modifiedTime"),
        "createdTime": item.get("createdTime"),
        "size": item.get("size"),
        "webViewLink": item.get("webViewLink"),
    }


class DriveOpsPlanner:
    def __init__(self, drive: GoogleDriveClient | None, audit_store: AuditStore) -> None:
        self.drive = drive
        self.audit = audit_store

    def _drive(self) -> GoogleDriveClient:
        if self.drive is None:
            raise RuntimeError("Google Drive client is required for this operation.")
        return self.drive

    def plan_organize_folder(
        self,
        *,
        folder_id_or_name: str,
        strategy: OrganizationStrategy,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        drive = self._drive()
        listing = drive.list_folder(folder_id_or_name, page_size=MAX_PLAN_ITEMS)
        if listing.get("has_more"):
            raise ValueError(
                f"Folder has more than {MAX_PLAN_ITEMS} items. "
                "Refine the request or organize a smaller folder to avoid an incomplete plan."
            )
        folder = listing["folder"]
        children = listing["files"]
        existing_folders = {
            f["name"]: f
            for f in children
            if f.get("mimeType") == GOOGLE_FOLDER_MIME and f.get("name")
        }

        target_names = sorted(
            {
                target_folder_name(f, strategy)
                for f in children
                if f.get("mimeType") != GOOGLE_FOLDER_MIME
            }
        )
        create_steps = [
            {
                "type": "create_folder",
                "folder_name": name,
                "parent_id": folder["id"],
                "status": "pending",
            }
            for name in target_names
            if name not in existing_folders
        ]

        move_steps = []
        for item in children:
            if item.get("mimeType") == GOOGLE_FOLDER_MIME:
                continue
            target_name = target_folder_name(item, strategy)
            target = existing_folders.get(target_name)
            source_parent = folder["id"]
            parents = item.get("parents", [])
            if target and target["id"] in parents:
                continue
            move_steps.append(
                {
                    "type": "move_file",
                    "file_id": item["id"],
                    "file_name": item["name"],
                    "source_parent_id": source_parent,
                    "target_folder_name": target_name,
                    "target_folder_id": target["id"] if target else None,
                    "original_parents": parents,
                    "status": "pending",
                }
            )

        plan_id = str(uuid.uuid4())
        plan = {
            "plan_id": plan_id,
            "status": "planned",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "strategy": strategy,
            "dry_run": bool(dry_run),
            "folder": {"id": folder["id"], "name": folder.get("name")},
            "confirmation": confirmation_for(plan_id),
            "undo_confirmation": undo_confirmation_for(plan_id),
            "summary": {
                "files_seen": len(children),
                "folders_to_create": len(create_steps),
                "files_to_move": len(move_steps),
            },
            "steps": create_steps + move_steps,
        }
        self.audit.save_plan(plan)
        self.audit.append_event(
            action="plan_created",
            status="ok",
            plan_id=plan_id,
            subject_id=folder["id"],
            after=plan["summary"],
            message=f"Created {strategy} organization plan.",
        )
        return self._plan_response(plan)

    def hygiene_report(
        self,
        *,
        folder_id_or_name: str = "My Drive",
        page_size: int = MAX_PLAN_ITEMS,
        stale_days: int = 365,
        large_mb: int = 100,
    ) -> dict[str, Any]:
        drive = self._drive()
        page_size = max(1, min(int(page_size), MAX_PLAN_ITEMS))
        listing = drive.list_folder(folder_id_or_name, page_size=page_size)
        folder = listing["folder"]
        files = listing["files"]
        non_folders = [f for f in files if f.get("mimeType") != GOOGLE_FOLDER_MIME]
        folders = [f for f in files if f.get("mimeType") == GOOGLE_FOLDER_MIME]

        duplicate_groups = self._duplicate_groups(non_folders)
        version_groups = self._version_groups(non_folders)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(stale_days)))
        stale_folders = [
            _compact_file(item)
            for item in folders
            if (dt := _parse_drive_time(item.get("modifiedTime"))) and dt < cutoff
        ]
        large_bytes = max(1, int(large_mb)) * 1024 * 1024
        large_binaries = [
            _compact_file(item)
            for item in non_folders
            if not (item.get("mimeType") or "").startswith("application/vnd.google-apps")
            and (_safe_int(item.get("size")) or 0) >= large_bytes
        ]
        sensitive_docs = [
            _compact_file(item)
            for item in non_folders
            if SENSITIVE_NAME_RE.search(item.get("name") or "")
        ]
        unmanaged_media = [
            _compact_file(item)
            for item in non_folders
            if (item.get("mimeType") or "").startswith(("image/", "video/"))
        ]
        loose_files = [_compact_file(item) for item in non_folders] if folder.get("id") == "root" else []

        findings = {
            "loose_root_files": loose_files[:50],
            "duplicate_names": duplicate_groups[:25],
            "version_groups": version_groups[:25],
            "stale_folders": stale_folders[:25],
            "large_binaries": large_binaries[:25],
            "sensitive_looking_docs": sensitive_docs[:25],
            "unmanaged_media": unmanaged_media[:25],
        }
        summary = {
            "files_scanned": len(files),
            "loose_root_files": len(loose_files),
            "duplicate_name_groups": len(duplicate_groups),
            "version_groups": len(version_groups),
            "stale_folders": len(stale_folders),
            "large_binaries": len(large_binaries),
            "sensitive_looking_docs": len(sensitive_docs),
            "unmanaged_media": len(unmanaged_media),
        }
        suggested_plans = []
        if duplicate_groups or version_groups:
            suggested_plans.append(
                {
                    "tool": "driveops.plan_duplicate_cleanup",
                    "folder_id_or_name": folder.get("name") or folder.get("id"),
                    "archive_folder_name": DEFAULT_ARCHIVE_FOLDER,
                    "description": "Archive older duplicate and version-looking files without deleting anything.",
                }
            )
        if unmanaged_media:
            suggested_plans.append(
                {
                    "tool": "driveops.plan_organize_folder",
                    "folder_id_or_name": folder.get("name") or folder.get("id"),
                    "strategy": "by_mime_type",
                    "description": "Group loose media and documents into type folders.",
                }
            )
        return {
            "status": "incomplete" if listing.get("has_more") else "ok",
            "folder": folder,
            "has_more": listing.get("has_more", False),
            "summary": summary,
            "findings": findings,
            "suggested_plans": suggested_plans,
            "message": "Report is limited to the scanned folder's immediate children.",
        }

    def plan_duplicate_cleanup(
        self,
        *,
        folder_id_or_name: str,
        archive_folder_name: str = DEFAULT_ARCHIVE_FOLDER,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        drive = self._drive()
        listing = drive.list_folder(folder_id_or_name, page_size=MAX_PLAN_ITEMS)
        if listing.get("has_more"):
            raise ValueError(
                f"Folder has more than {MAX_PLAN_ITEMS} items. "
                "Refine the request or clean up a smaller folder to avoid an incomplete plan."
            )
        folder = listing["folder"]
        children = listing["files"]
        files = [f for f in children if f.get("mimeType") != GOOGLE_FOLDER_MIME]
        existing_archive = next(
            (
                f
                for f in children
                if f.get("mimeType") == GOOGLE_FOLDER_MIME
                and (f.get("name") or "").lower() == archive_folder_name.lower()
            ),
            None,
        )
        cleanup_groups = self._cleanup_groups(files)
        move_steps = []
        for group in cleanup_groups:
            keep = group["keep"]
            for item in group["archive"]:
                move_steps.append(
                    {
                        "type": "move_file",
                        "file_id": item["id"],
                        "file_name": item["name"],
                        "source_parent_id": folder["id"],
                        "target_folder_name": archive_folder_name,
                        "target_folder_id": existing_archive["id"] if existing_archive else None,
                        "original_parents": item.get("parents", []),
                        "status": "pending",
                        "reason": group["reason"],
                        "group_key": group["group_key"],
                        "keep_file": _compact_file(keep),
                    }
                )

        create_steps = []
        if move_steps and existing_archive is None:
            create_steps.append(
                {
                    "type": "create_folder",
                    "folder_name": archive_folder_name,
                    "parent_id": folder["id"],
                    "status": "pending",
                }
            )

        summary = {
            "files_seen": len(files),
            "groups_found": len(cleanup_groups),
            "folders_to_create": len(create_steps),
            "files_to_move": len(move_steps),
            "files_to_archive": len(move_steps),
            "files_to_delete": 0,
        }
        if not move_steps:
            return {
                "plan_id": None,
                "status": "no_changes",
                "strategy": "duplicate_cleanup",
                "folder": {"id": folder["id"], "name": folder.get("name")},
                "summary": summary,
                "confirmation": None,
                "undo_confirmation": None,
                "steps_total": 0,
                "steps_returned": 0,
                "steps_truncated": False,
                "step_groups": [],
                "steps": [],
                "message": "No duplicate-name or version-like files were found, so no plan was stored.",
            }

        plan_id = str(uuid.uuid4())
        plan = {
            "plan_id": plan_id,
            "status": "planned",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "strategy": "duplicate_cleanup",
            "dry_run": bool(dry_run),
            "folder": {"id": folder["id"], "name": folder.get("name")},
            "confirmation": confirmation_for(plan_id),
            "undo_confirmation": undo_confirmation_for(plan_id),
            "summary": summary,
            "steps": create_steps + move_steps,
        }
        self.audit.save_plan(plan)
        self.audit.append_event(
            action="plan_created",
            status="ok",
            plan_id=plan_id,
            subject_id=folder["id"],
            after=plan["summary"],
            message="Created duplicate/version cleanup plan.",
        )
        return self._plan_response(plan)

    @staticmethod
    def _duplicate_groups(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_name: dict[str, list[dict[str, Any]]] = {}
        for item in files:
            key = (item.get("name") or "").strip().lower()
            if key:
                by_name.setdefault(key, []).append(item)
        groups = []
        for key, items in by_name.items():
            if len(items) < 2:
                continue
            ordered = sorted(items, key=_file_sort_time, reverse=True)
            groups.append(
                {
                    "group_key": key,
                    "reason": "same_name",
                    "count": len(ordered),
                    "keep": _compact_file(ordered[0]),
                    "candidates": [_compact_file(item) for item in ordered[1:]],
                }
            )
        return sorted(groups, key=lambda item: item["group_key"])

    @staticmethod
    def _version_groups(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_base: dict[str, list[dict[str, Any]]] = {}
        version_like_keys: set[str] = set()
        for item in files:
            name = item.get("name") or ""
            key = _version_group_key(name)
            if not key:
                continue
            by_base.setdefault(key, []).append(item)
            if key != name.lower().strip():
                version_like_keys.add(key)
        groups = []
        for key, items in by_base.items():
            if key not in version_like_keys or len(items) < 2:
                continue
            ordered = sorted(items, key=_file_sort_time, reverse=True)
            groups.append(
                {
                    "group_key": key,
                    "reason": "version_like_name",
                    "count": len(ordered),
                    "keep": _compact_file(ordered[0]),
                    "candidates": [_compact_file(item) for item in ordered[1:]],
                }
            )
        return sorted(groups, key=lambda item: item["group_key"])

    def _cleanup_groups(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen_archive_ids: set[str] = set()
        cleanup_groups = []
        for group in self._duplicate_groups(files) + self._version_groups(files):
            archive = []
            for item in group["candidates"]:
                file_id = item["id"]
                if file_id in seen_archive_ids:
                    continue
                original = next((f for f in files if f.get("id") == file_id), None)
                if original is None:
                    continue
                archive.append(original)
                seen_archive_ids.add(file_id)
            if archive:
                keep = next((f for f in files if f.get("id") == group["keep"]["id"]), group["keep"])
                cleanup_groups.append(
                    {
                        "group_key": group["group_key"],
                        "reason": group["reason"],
                        "keep": keep,
                        "archive": archive,
                    }
                )
        return cleanup_groups

    def preview_plan(
        self,
        plan_id: str | None = None,
        *,
        detail: str = "summary",
        max_steps: int = 20,
    ) -> dict[str, Any]:
        plan = self.audit.get_plan(plan_id) if plan_id else self.audit.latest_plan()
        return self._plan_response(plan, detail=detail, max_steps=max_steps)

    @staticmethod
    def _step_groups(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for step in steps:
            if step["type"] == "create_folder":
                key = step["folder_name"]
                group = groups.setdefault(key, {"target": key, "folders_to_create": 0, "files_to_move": 0})
                group["folders_to_create"] += 1
            elif step["type"] == "move_file":
                key = step["target_folder_name"]
                group = groups.setdefault(key, {"target": key, "folders_to_create": 0, "files_to_move": 0})
                group["files_to_move"] += 1
        return sorted(groups.values(), key=lambda item: item["target"])

    def _plan_response(
        self,
        plan: dict[str, Any],
        *,
        detail: str = "summary",
        max_steps: int = 20,
    ) -> dict[str, Any]:
        steps = plan["steps"]
        max_steps = max(0, min(int(max_steps), 200))
        include_full = detail == "full"
        visible_steps = steps if include_full else steps[:max_steps]
        return {
            "plan_id": plan["plan_id"],
            "status": plan["status"],
            "strategy": plan["strategy"],
            "folder": plan["folder"],
            "summary": plan["summary"],
            "confirmation": plan["confirmation"],
            "undo_confirmation": plan["undo_confirmation"],
            "steps_total": len(steps),
            "steps_returned": len(visible_steps),
            "steps_truncated": len(visible_steps) < len(steps),
            "step_groups": self._step_groups(steps),
            "steps": visible_steps,
        }

    def apply_plan(self, *, plan_id: str | None = None, confirmation: str) -> dict[str, Any]:
        require_write_profile()
        plan = self.audit.get_plan(plan_id) if plan_id else self.audit.latest_plan(status="planned")
        plan_id = plan["plan_id"]
        if plan["status"] != "planned":
            raise ValueError(f"Plan {plan_id} is {plan['status']}; only planned plans can be applied.")
        if confirmation != plan["confirmation"]:
            raise ValueError("Confirmation string does not match plan preview.")
        if not plan["steps"]:
            raise ValueError("Refusing to apply an empty plan.")

        created_by_name: dict[str, str] = {}
        try:
            for step in plan["steps"]:
                if step["type"] != "create_folder":
                    continue
                drive = self._drive()
                existing = drive.find_child_folder(step["parent_id"], step["folder_name"])
                folder = existing or drive.create_folder(step["folder_name"], step["parent_id"])
                step["created_folder_id"] = folder["id"]
                step["status"] = "applied"
                created_by_name[step["folder_name"]] = folder["id"]
                self.audit.append_event(
                    action="create_folder",
                    status="ok",
                    plan_id=plan_id,
                    subject_id=folder["id"],
                    after=folder,
                    message=f"Ensured folder {step['folder_name']}.",
                )

            for step in plan["steps"]:
                if step["type"] != "move_file":
                    continue
                drive = self._drive()
                current = drive.get_file(step["file_id"], fields="id,name,mimeType,parents,modifiedTime")
                parents = current.get("parents", [])
                if step["source_parent_id"] not in parents:
                    raise RuntimeError(
                        f"Stale plan: file {step['file_name']} no longer has source parent {step['source_parent_id']}."
                    )
                target_id = step.get("target_folder_id") or created_by_name.get(step["target_folder_name"])
                if not target_id:
                    target = drive.find_child_folder(plan["folder"]["id"], step["target_folder_name"])
                    if not target:
                        raise RuntimeError(f"Target folder {step['target_folder_name']} was not created.")
                    target_id = target["id"]
                moved = drive.move_file(step["file_id"], target_id, step["source_parent_id"])
                step["target_folder_id"] = target_id
                step["after_parents"] = moved.get("parents", [])
                step["status"] = "applied"
                self.audit.append_event(
                    action="move_file",
                    status="ok",
                    plan_id=plan_id,
                    subject_id=step["file_id"],
                    before={"parents": parents},
                    after={"parents": moved.get("parents", [])},
                    message=f"Moved {step['file_name']} to {step['target_folder_name']}.",
                )
        except Exception as exc:
            self.audit.append_event(
                action="apply_plan",
                status="failed",
                plan_id=plan_id,
                subject_id=plan["folder"]["id"],
                message=str(exc),
            )
            self.audit.update_plan(plan, status="failed", error=str(exc))
            raise

        self.audit.update_plan(plan, status="applied")
        self.audit.append_event(
            action="apply_plan",
            status="ok",
            plan_id=plan_id,
            subject_id=plan["folder"]["id"],
            after=plan["summary"],
            message="Plan applied successfully.",
        )
        return {"plan_id": plan_id, "status": "applied", "summary": plan["summary"]}

    def undo_plan(self, *, plan_id: str | None = None, confirmation: str) -> dict[str, Any]:
        require_write_profile()
        plan = self.audit.get_plan(plan_id) if plan_id else self.audit.latest_plan(status="applied")
        plan_id = plan["plan_id"]
        if plan["status"] != "applied":
            raise ValueError(f"Plan {plan_id} is {plan['status']}; only applied plans can be undone.")
        if confirmation != plan["undo_confirmation"]:
            raise ValueError("Undo confirmation string does not match plan preview.")

        undone = 0
        try:
            for step in reversed(plan["steps"]):
                if step["type"] != "move_file" or step.get("status") != "applied":
                    continue
                target_id = step.get("target_folder_id")
                if not target_id:
                    continue
                drive = self._drive()
                current = drive.get_file(step["file_id"], fields="id,name,mimeType,parents,modifiedTime")
                parents = current.get("parents", [])
                if target_id not in parents:
                    raise RuntimeError(
                        f"Cannot undo: file {step['file_name']} is no longer in planned target folder."
                    )
                moved = drive.move_file(step["file_id"], step["source_parent_id"], target_id)
                step["undo_status"] = "undone"
                undone += 1
                self.audit.append_event(
                    action="undo_move",
                    status="ok",
                    plan_id=plan_id,
                    subject_id=step["file_id"],
                    before={"parents": parents},
                    after={"parents": moved.get("parents", [])},
                    message=f"Moved {step['file_name']} back to original parent.",
                )
        except Exception as exc:
            self.audit.append_event(
                action="undo_plan",
                status="failed",
                plan_id=plan_id,
                subject_id=plan["folder"]["id"],
                message=str(exc),
            )
            raise

        self.audit.update_plan(plan, status="undone")
        self.audit.append_event(
            action="undo_plan",
            status="ok",
            plan_id=plan_id,
            subject_id=plan["folder"]["id"],
            after={"files_restored": undone},
            message="Plan undone successfully. Created folders are left in place.",
        )
        return {"plan_id": plan_id, "status": "undone", "files_restored": undone}
