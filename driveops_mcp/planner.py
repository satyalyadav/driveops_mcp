"""Safe DriveOps plan generation, application, and undo logic."""

from __future__ import annotations

import re
import uuid
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


class DriveOpsPlanner:
    def __init__(self, drive: GoogleDriveClient, audit_store: AuditStore) -> None:
        self.drive = drive
        self.audit = audit_store

    def plan_organize_folder(
        self,
        *,
        folder_id_or_name: str,
        strategy: OrganizationStrategy,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        listing = self.drive.list_folder(folder_id_or_name, page_size=1000)
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
        return plan

    def preview_plan(self, plan_id: str | None = None) -> dict[str, Any]:
        plan = self.audit.get_plan(plan_id) if plan_id else self.audit.latest_plan()
        return {
            "plan_id": plan["plan_id"],
            "status": plan["status"],
            "strategy": plan["strategy"],
            "folder": plan["folder"],
            "summary": plan["summary"],
            "confirmation": plan["confirmation"],
            "undo_confirmation": plan["undo_confirmation"],
            "steps": plan["steps"],
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
                existing = self.drive.find_child_folder(step["parent_id"], step["folder_name"])
                folder = existing or self.drive.create_folder(step["folder_name"], step["parent_id"])
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
                current = self.drive.get_file(step["file_id"], fields="id,name,mimeType,parents,modifiedTime")
                parents = current.get("parents", [])
                if step["source_parent_id"] not in parents:
                    raise RuntimeError(
                        f"Stale plan: file {step['file_name']} no longer has source parent {step['source_parent_id']}."
                    )
                target_id = step.get("target_folder_id") or created_by_name.get(step["target_folder_name"])
                if not target_id:
                    target = self.drive.find_child_folder(plan["folder"]["id"], step["target_folder_name"])
                    if not target:
                        raise RuntimeError(f"Target folder {step['target_folder_name']} was not created.")
                    target_id = target["id"]
                moved = self.drive.move_file(step["file_id"], target_id, step["source_parent_id"])
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
                current = self.drive.get_file(step["file_id"], fields="id,name,mimeType,parents,modifiedTime")
                parents = current.get("parents", [])
                if target_id not in parents:
                    raise RuntimeError(
                        f"Cannot undo: file {step['file_name']} is no longer in planned target folder."
                    )
                moved = self.drive.move_file(step["file_id"], step["source_parent_id"], target_id)
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
