"""MCP server entrypoints and tool registration."""

from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from mcp.server import MCPServer

from . import __version__
from .audit import AuditStore
from .auth import auth_status, get_credentials, logout, profile_from_name, require_write_profile
from .google_drive import GoogleDriveClient
from .planner import DriveOpsPlanner
from .schemas import OrganizationStrategy

INSTRUCTIONS = """DriveOps MCP is a safe Google Drive operations layer. Use read/search tools freely. For organization or writes, first create a plan, preview it, explain the proposed changes, and only then call apply with the confirmation string from the preview. Never invent confirmation strings. Undo uses the undo confirmation from preview. Prefer small folders and ask the user before broad Drive changes."""

_drive_factory: Callable[[], GoogleDriveClient] = GoogleDriveClient
_store_factory: Callable[[], AuditStore] = AuditStore


def set_factories(
    *,
    drive_factory: Callable[[], GoogleDriveClient] | None = None,
    store_factory: Callable[[], AuditStore] | None = None,
) -> None:
    """Override factories for tests."""

    global _drive_factory, _store_factory
    if drive_factory is not None:
        _drive_factory = drive_factory
    if store_factory is not None:
        _store_factory = store_factory


def _drive() -> GoogleDriveClient:
    return _drive_factory()


def _planner() -> DriveOpsPlanner:
    return DriveOpsPlanner(_drive_factory(), _store_factory())


def build_server() -> MCPServer:
    mcp = MCPServer(
        name="driveops-mcp",
        title="DriveOps MCP",
        description="Open-source safe Google Drive operations with plans, approvals, undo, and audit logs.",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    @mcp.tool(
        name="drive.search_files",
        description="Search Google Drive files. This is read-only and returns metadata plus Drive links.",
        structured_output=True,
    )
    def search_files(
        query: str,
        folder_id: str | None = None,
        mime_types: list[str] | None = None,
        page_size: int = 10,
    ) -> dict[str, Any]:
        return _drive().search_files(
            query=query,
            folder_id=folder_id,
            mime_types=mime_types,
            page_size=page_size,
        )

    @mcp.tool(
        name="drive.read_file",
        description="Read a Google Drive file by ID, exact filename, or search text. Text is returned when safe; binary files return metadata and download hints.",
        structured_output=True,
    )
    def read_file(file_id_or_name: str, export_format: str | None = None) -> dict[str, Any]:
        return _drive().read_file(file_id_or_name, export_format=export_format)

    @mcp.tool(
        name="drive.list_folder",
        description="Resolve a folder ID or name and list its immediate children.",
        structured_output=True,
    )
    def list_folder(folder_id_or_name: str, page_size: int = 100) -> dict[str, Any]:
        return _drive().list_folder(folder_id_or_name, page_size=page_size)

    @mcp.tool(
        name="drive.get_changes",
        description="List files in a folder modified since an ISO date such as 2026-06-29.",
        structured_output=True,
    )
    def get_changes(folder_id_or_name: str, since: str, page_size: int = 100) -> dict[str, Any]:
        return _drive().get_changes(folder_id_or_name, since, page_size=page_size)

    @mcp.tool(
        name="driveops.plan_organize_folder",
        description="Create a stored safe organization plan for a folder. This never mutates Drive.",
        structured_output=True,
    )
    def plan_organize_folder(
        folder_id_or_name: str,
        strategy: OrganizationStrategy,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        return _planner().plan_organize_folder(
            folder_id_or_name=folder_id_or_name,
            strategy=strategy,
            dry_run=dry_run,
        )

    @mcp.tool(
        name="driveops.preview_plan",
        description="Preview a stored DriveOps plan and retrieve confirmation strings. Omit plan_id to preview the latest plan. Use detail='full' only when every step is needed.",
        structured_output=True,
    )
    def preview_plan(
        plan_id: str | None = None,
        detail: str = "summary",
        max_steps: int = 20,
    ) -> dict[str, Any]:
        return _planner().preview_plan(plan_id, detail=detail, max_steps=max_steps)

    @mcp.tool(
        name="driveops.apply_plan",
        description="Apply a stored DriveOps plan. Omit plan_id to apply the latest planned plan. Requires write profile and exact confirmation.",
        structured_output=True,
    )
    def apply_plan(confirmation: str, plan_id: str | None = None) -> dict[str, Any]:
        require_write_profile()
        return _planner().apply_plan(plan_id=plan_id, confirmation=confirmation)

    @mcp.tool(
        name="driveops.undo_plan",
        description="Undo file moves from an applied DriveOps plan. Omit plan_id to undo the latest applied plan. Requires write profile and exact undo confirmation.",
        structured_output=True,
    )
    def undo_plan(confirmation: str, plan_id: str | None = None) -> dict[str, Any]:
        require_write_profile()
        return _planner().undo_plan(plan_id=plan_id, confirmation=confirmation)

    @mcp.tool(
        name="driveops.list_audit_events",
        description="List DriveOps audit events, optionally scoped to a plan.",
        structured_output=True,
    )
    def list_audit_events(limit: int = 50, plan_id: str | None = None) -> dict[str, Any]:
        events = _store_factory().list_events(limit=limit, plan_id=plan_id)
        return {"count": len(events), "events": events}

    @mcp.tool(
        name="gdrive_search",
        description="Compatibility alias for drive.search_files.",
        structured_output=True,
    )
    def gdrive_search(
        query: str = "*",
        folderId: str | None = None,
        pageSize: int = 10,
    ) -> dict[str, Any]:
        return search_files(query=query, folder_id=folderId, page_size=pageSize)

    @mcp.tool(
        name="gdrive_read_file",
        description="Compatibility alias for drive.read_file.",
        structured_output=True,
    )
    def gdrive_read_file(fileId: str) -> dict[str, Any]:
        return read_file(file_id_or_name=fileId)

    return mcp


def run_stdio() -> None:
    build_server().run("stdio")


def run_http(host: str = "127.0.0.1", port: int = 8787) -> None:
    build_server().run(
        "streamable-http",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="driveops-mcp")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("stdio", help="Run the MCP server over stdio.")
    http = sub.add_parser("http", help="Run the MCP server over Streamable HTTP.")
    http.add_argument("--host", default="127.0.0.1")
    http.add_argument("--port", type=int, default=8787)
    auth = sub.add_parser("auth", help="Manage local Google OAuth for DriveOps.")
    auth_sub = auth.add_subparsers(dest="auth_command")
    auth_status_parser = auth_sub.add_parser("status", help="Show local auth status.")
    auth_status_parser.add_argument("--profile", choices=["readonly", "write"])
    auth_login = auth_sub.add_parser("login", help="Open browser sign-in and save a token.")
    auth_login.add_argument("--profile", choices=["readonly", "write"], default=None)
    auth_login.add_argument("--force", action="store_true", help="Discard existing token first.")
    auth_logout = auth_sub.add_parser("logout", help="Remove the saved local token.")
    auth_logout.add_argument("--yes", action="store_true", help="Confirm token removal.")
    args = parser.parse_args(argv)
    if args.command in {None, "stdio"}:
        try:
            run_stdio()
        except KeyboardInterrupt:
            return
    elif args.command == "http":
        try:
            run_http(host=args.host, port=args.port)
        except KeyboardInterrupt:
            return
    elif args.command == "auth":
        if args.auth_command in {None, "status"}:
            profile = profile_from_name(getattr(args, "profile", None))
            print(json.dumps(auth_status(profile), indent=2))
        elif args.auth_command == "login":
            profile = profile_from_name(args.profile)
            get_credentials(profile, force_reauth=args.force, show_auth_url=True)
            print(json.dumps(auth_status(profile), indent=2))
        elif args.auth_command == "logout":
            if not args.yes:
                parser.error("auth logout requires --yes")
            removed = logout()
            print(json.dumps({"token_removed": removed}, indent=2))
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
