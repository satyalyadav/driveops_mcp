from __future__ import annotations

import pytest

from mcp import Client

from driveops_mcp.audit import AuditStore
from driveops_mcp.server import build_server, set_factories
from driveops_mcp.schemas import confirmation_for, now_iso, undo_confirmation_for
from tests.test_planner import FakeDrive


@pytest.mark.asyncio
async def test_mcp_lists_expected_tools(tmp_path):
    drive = FakeDrive()
    set_factories(
        drive_factory=lambda: drive,
        store_factory=lambda: AuditStore(tmp_path / "driveops.db"),
    )
    async with Client(build_server()) as client:
        tools = await client.list_tools()
    names = {tool.name for tool in tools.tools}
    assert "drive.search_files" in names
    assert "drive.get_changes" in names
    assert "driveops.plan_organize_folder" in names
    assert "gdrive_search" in names


@pytest.mark.asyncio
async def test_mcp_can_call_plan_tool(tmp_path):
    drive = FakeDrive()
    set_factories(
        drive_factory=lambda: drive,
        store_factory=lambda: AuditStore(tmp_path / "driveops.db"),
    )
    async with Client(build_server()) as client:
        result = await client.call_tool(
            "driveops.plan_organize_folder",
            {"folder_id_or_name": "folder_root", "strategy": "by_created_month"},
        )
    assert result.structured_content["status"] == "planned"
    assert result.structured_content["summary"]["files_to_move"] == 2


@pytest.mark.asyncio
async def test_mcp_preview_plan_does_not_construct_drive_client(tmp_path):
    store = AuditStore(tmp_path / "driveops.db")
    plan_id = "preview-local-only"
    store.save_plan(
        {
            "plan_id": plan_id,
            "status": "planned",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "strategy": "by_created_month",
            "dry_run": True,
            "folder": {"id": "folder_root", "name": "Root"},
            "confirmation": confirmation_for(plan_id),
            "undo_confirmation": undo_confirmation_for(plan_id),
            "summary": {"files_seen": 0, "folders_to_create": 0, "files_to_move": 0},
            "steps": [],
        }
    )

    def fail_drive_factory():
        raise AssertionError("preview_plan should not need Google Drive credentials")

    set_factories(
        drive_factory=fail_drive_factory,
        store_factory=lambda: store,
    )
    async with Client(build_server()) as client:
        result = await client.call_tool("driveops.preview_plan", {"plan_id": plan_id})

    assert result.structured_content["plan_id"] == plan_id
