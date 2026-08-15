# DriveOps MCP

> Originally ported to Python from [isaacphi/mcp-gdrive](https://github.com/isaacphi/mcp-gdrive).

> [!IMPORTANT]
> **Project status:** DriveOps MCP is no longer under active development. It remains available as an open-source reference and portfolio project, but new features, support, and security updates are not planned.

DriveOps MCP is a free, open-source, model-agnostic Google Drive toolkit for AI agents. It lets MCP-compatible assistants search, read, organize, and safely modify Drive files.

Writes are planned, previewed, confirmed, audited, and reversible when possible.

https://github.com/user-attachments/assets/3ab2b67e-b6b5-4a6a-9685-25fffdd5cd2a

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

## Google OAuth Setup

Create a Google OAuth Desktop client with the Google Drive API and Google Sheets
API enabled. For durable credentials, the Google Auth Platform publishing status
must not remain **External + Testing**: Google expires those refresh tokens after
seven days. Use **Internal** for one Workspace organization, or change an External
app's Audience publishing status to **In production**. See the
[complete OAuth setup](docs/google-oauth-setup.md).

Save the downloaded client secret at:

```text
~/.config/driveops-mcp/client_secret.json
```

Or configure explicit paths:

```bash
export DRIVEOPS_GOOGLE_CLIENT_SECRET=/absolute/path/client_secret.json
export DRIVEOPS_GOOGLE_TOKEN=/absolute/path/token.json
```

DriveOps uses read-only access by default. To enable write tools:

```bash
export DRIVEOPS_SCOPE_PROFILE=write
```

Check or start Google authorization. When `--profile` is omitted, all commands
honor `DRIVEOPS_SCOPE_PROFILE`:

```bash
driveops-mcp auth status
driveops-mcp auth login
driveops-mcp auth check
```

You can also select the profile explicitly:

```bash
driveops-mcp auth status --profile write
driveops-mcp auth login --profile write
```

Changing from read-only to write access requires a new Google consent flow so
the saved token includes the broader scopes.

`auth status` is a local inspection. `auth check` performs a real token refresh
against Google and exits nonzero if the saved credential is missing, rejected,
or not refreshable. Run it once after setup and before unattended use.

## Run

Stdio:

```bash
driveops-mcp stdio
```

Streamable HTTP:

```bash
driveops-mcp http --host 127.0.0.1 --port 8787
```

The HTTP endpoint is `/mcp`. See [client setup](docs/client-setup.md) for Codex, Claude Code, Gemini CLI, and Cursor configuration. Before exposing HTTP publicly, read the [deployment guide](docs/public-deployment.md).

## What It Can Do

- Search, read, list, download, and export Drive files.
- Extract text from Google Workspace files, PDFs, Office documents, and ZIP archives.
- Create, upload, rename, copy, move, trash, restore, and delete files.
- Inspect and manage sharing permissions.
- Work with shared drives and Drive change feeds.
- Find clutter, duplicates, old files, large files, and risky sharing.
- Create organization and cleanup plans without immediately changing Drive.
- Preview, approve, audit, and undo supported changes.

## Example Prompts

```text
Search my Drive for "satellite simulator" and summarize the top matches.
```

```text
Read the file named "June application notes". If there are multiple matches, show me the options first.
```

```text
Run a Drive hygiene report on My Drive and recommend safe cleanup plans.
```

```text
Find duplicate or version-looking files in my "Applications" folder and create a safe archive plan. Do not delete anything.
```

```text
Create a plan to organize my "Screenshots" folder by modified month. Show me the proposed changes without applying them.
```

```text
Preview the latest plan, explain it, and apply it only after I approve it.
```

## Alternatives

For a broader, actively maintained integration covering Drive, Docs, Sheets, Slides, and Calendar, see [Google Drive MCP Server](https://github.com/piotr-agier/google-drive-mcp).

See [`docs/`](docs/) for security, client configuration, hosting, and protocol details.
