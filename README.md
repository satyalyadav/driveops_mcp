# DriveOps MCP

DriveOps MCP is an open-source, model-agnostic Google Drive operations layer for AI agents. It is not another raw Drive connector. Google, ChatGPT, Claude, Gemini, and Google Workspace CLI already cover large parts of that space.

DriveOps MCP focuses on a different job: **safe Drive operations**. Agents can search and read Drive, then propose file-organization plans, preview them, apply them only with explicit confirmation, undo applied moves, and inspect an audit log.

## Why This Exists

Google's official Drive MCP server already exposes raw Drive tools such as search, read, create, copy, download, permissions, and recent files. Gemini in Drive adds native AI search and summaries. ChatGPT and Claude have their own Drive connectors. Those are useful, but they are tied to specific products and do not provide a portable, open, auditable DriveOps workflow.

DriveOps MCP adds:

- plan-preview-approve-apply-undo workflows;
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
- `drive.get_changes`

Safe DriveOps:

- `driveops.plan_organize_folder`
- `driveops.preview_plan`
- `driveops.apply_plan`
- `driveops.undo_plan`
- `driveops.list_audit_events`

Compatibility aliases:

- `gdrive_search`
- `gdrive_read_file`

The old direct destructive `gdrive_organize` tool is intentionally removed. Use `driveops.plan_organize_folder`, inspect the plan, then call `driveops.apply_plan` with the confirmation string.

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
