from __future__ import annotations

import threading

import pytest

from mcp import Client

from driveops_mcp.audit import AuditStore
from driveops_mcp import server as server_module
from driveops_mcp.server import build_server, set_factories
from driveops_mcp.schemas import confirmation_for, now_iso, undo_confirmation_for
from tests.test_planner import ActionFakeDrive, FakeDrive


def test_drive_factory_is_cached_per_thread_and_invalidated() -> None:
    created: list[object] = []
    creation_lock = threading.Lock()

    def factory():
        with creation_lock:
            client = object()
            created.append(client)
            return client

    set_factories(drive_factory=factory)  # type: ignore[arg-type]
    clients_by_thread: list[tuple[object, object]] = []
    start = threading.Barrier(2)

    def get_twice() -> None:
        start.wait()
        clients_by_thread.append((server_module._drive(), server_module._drive()))

    threads = [threading.Thread(target=get_twice) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 2
    assert all(first is second for first, second in clients_by_thread)
    assert clients_by_thread[0][0] is not clients_by_thread[1][0]

    replacement = object()
    set_factories(drive_factory=lambda: replacement)  # type: ignore[arg-type]
    assert server_module._drive() is replacement


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
    assert "drive.download_file" in names
    assert "drive.extract_file" in names
    assert "drive.list_permissions" in names
    assert "drive.list_shared_drives" in names
    assert "drive.get_change_token" in names
    assert "drive.list_changes" in names
    assert "driveops.hygiene_report" in names
    assert "driveops.plan_organize_folder" in names
    assert "driveops.plan_duplicate_cleanup" in names
    assert "driveops.plan_file_actions" in names
    assert "gdrive_search" in names
    action_tool = next(
        tool for tool in tools.tools if tool.name == "driveops.plan_file_actions"
    )
    action_schema = action_tool.input_schema["$defs"]["DriveFileAction"]
    assert "delete_file" in action_schema["properties"]["type"]["enum"]
    assert "permission_id" in action_schema["properties"]


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
async def test_mcp_can_call_hygiene_report(tmp_path):
    drive = FakeDrive()
    set_factories(
        drive_factory=lambda: drive,
        store_factory=lambda: AuditStore(tmp_path / "driveops.db"),
    )
    async with Client(build_server()) as client:
        result = await client.call_tool(
            "driveops.hygiene_report",
            {"folder_id_or_name": "Root"},
        )

    assert result.structured_content["status"] == "ok"
    assert "duplicate_name_groups" in result.structured_content["summary"]


@pytest.mark.asyncio
async def test_mcp_can_plan_general_file_action(tmp_path):
    drive = ActionFakeDrive()
    set_factories(
        drive_factory=lambda: drive,
        store_factory=lambda: AuditStore(tmp_path / "driveops.db"),
    )
    async with Client(build_server()) as client:
        result = await client.call_tool(
            "driveops.plan_file_actions",
            {
                "actions": [
                    {
                        "type": "rename_file",
                        "file_id_or_name": "f1",
                        "new_name": "new-name.pdf",
                    }
                ]
            },
        )

    assert result.structured_content["summary"]["action_counts"] == {"rename_file": 1}


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
