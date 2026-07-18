# DriveOps MCP

DriveOps MCP is a free, open-source, model-agnostic Google Drive operations layer for AI agents. It combines the everyday Drive capabilities expected from a general connector—search, upload, download, file management, sharing, shared drives, and change tracking—with safe organization workflows that can run with any MCP-compatible LLM or infrastructure.

All writes use **safe Drive operations**: agents first create a stored plan, preview it, apply it only with explicit confirmation, undo reversible actions, and inspect an audit log. Permanent deletion is clearly marked irreversible.

## Why This Exists

Official Drive tools and bundled AI features are often tied to a particular product, plan, or host. DriveOps MCP provides a portable, self-hostable alternative without removing the safety controls needed when an LLM can modify user files.

DriveOps MCP adds:

- plan-preview-approve-apply-undo workflows;
- Drive hygiene reports for clutter, duplicate names, stale folders, large binaries, sensitive-looking filenames, and unmanaged media;
- duplicate/version cleanup plans that archive older candidates without deleting files by default;
- general file action plans for create/upload, rename, copy, move, trash/restore/delete, and sharing;
- real downloads and Workspace exports, plus PDF/Office text and safe ZIP extraction;
- permission inspection, shared-drive discovery, pagination, and the real Drive Changes feed;
- SQLite audit logs for every write step;
- stale-plan checks before moving files;
- local stdio and Streamable HTTP transports;
- portable setup for Codex, Claude Code, Gemini CLI, Cursor, and deployable HTTP clients.

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

The project intentionally pins the MCP Python SDK prerelease:

```text
mcp[cli]==2.0.0a3
```

This targets the MCP `2026-07-28` release candidate. Expect SDK churn until the final spec and stable SDK land.

## Google OAuth Setup

Create a Google OAuth Desktop client with Drive API enabled. Put the client secret at:

```text
~/.config/driveops-mcp/client_secret.json
```

or set:

```bash
export DRIVEOPS_GOOGLE_CLIENT_SECRET=/absolute/path/client_secret.json
export DRIVEOPS_GOOGLE_TOKEN=/absolute/path/token.json
```

Read-only scopes are used by default. Write tools require:

```bash
export DRIVEOPS_SCOPE_PROFILE=write
```

Check or start local auth from a terminal:

```bash
driveops-mcp auth status
driveops-mcp auth login
```

For write testing:

```bash
DRIVEOPS_SCOPE_PROFILE=write driveops-mcp auth login --profile write --force
```

Local state defaults to:

```text
~/.local/share/driveops-mcp/driveops.db
```

Override it with:

```bash
export DRIVEOPS_STATE_DIR=/path/to/state-dir
```

## Run

Stdio:

```bash
driveops-mcp stdio
```

Streamable HTTP:

```bash
driveops-mcp http --host 127.0.0.1 --port 8787
```

The HTTP endpoint is `/mcp`.

## Public Tools

Read/search:

- `drive.search_files`
- `drive.read_file` (accepts a file ID, exact filename, or search text)
- `drive.list_folder`
- `drive.download_file`
- `drive.extract_file`
- `drive.list_permissions`
- `drive.list_shared_drives`
- `drive.get_change_token`
- `drive.list_changes`
- `drive.get_changes` (legacy modified-since folder query)

Safe DriveOps:

- `driveops.hygiene_report`
- `driveops.plan_organize_folder`
- `driveops.plan_duplicate_cleanup`
- `driveops.plan_file_actions`
- `driveops.preview_plan`
- `driveops.apply_plan`
- `driveops.undo_plan`
- `driveops.list_audit_events`

Compatibility aliases:

- `gdrive_search`
- `gdrive_read_file`

The old direct destructive `gdrive_organize` tool is intentionally removed. Use `driveops.plan_organize_folder`, inspect the plan, then call `driveops.apply_plan` with the confirmation string.

`driveops.plan_file_actions` supports these action types:

- `create_folder`, `create_file`, `upload_file`;
- `rename_file`, `copy_file`, `move_file`;
- `trash_file`, `restore_file`, `delete_file`;
- `share_file`, `update_permission`, `remove_permission`.

`delete_file` permanently deletes and makes the entire plan non-undoable. Prefer `trash_file` for normal cleanup.

## Example Prompts

These examples are written the way a user should ask an agent. The agent can use returned file IDs and plan IDs internally.

```text
Search my Drive for "satellite simulator" and summarize the top matches.
```

```text
Read the file named "June application notes" from Drive. If there are multiple matches, show me the options first.
```

```text
List my "Applications" folder and tell me which files look stale.
```

```text
Run a Drive hygiene report on My Drive. Tell me what looks messy and what cleanup plans you recommend.
```

```text
Find duplicate or version-looking files in my "Applications" folder and create a safe archive plan. Do not delete anything.
```

```text
Create a plan to organize my "Screenshots" folder by modified month. Show me the proposed folders and moves, but do not apply anything yet.
```

```text
Preview the latest DriveOps plan and explain the proposed changes in plain English.
```

```text
I approve the latest DriveOps plan. Use the confirmation from the preview and apply it.
```

```text
Undo the most recently applied DriveOps plan. Use the undo confirmation from the preview.
```

## Tests

```bash
.venv/bin/python -m pytest
```

Live tests are optional and gated:

```bash
export DRIVEOPS_LIVE_TEST_FOLDER_ID=<folder-id>
export DRIVEOPS_LIVE_WRITE_TESTS=1
```

See `docs/` for client setup, security notes, MCP 2026 notes, and comparison with existing Drive AI/connectors.
