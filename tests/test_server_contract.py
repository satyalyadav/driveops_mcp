from __future__ import annotations

import pytest

from mcp import Client

from driveops_mcp.audit import AuditStore
from driveops_mcp.server import build_server, set_factories
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
