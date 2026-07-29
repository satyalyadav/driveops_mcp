# Client Setup

DriveOps MCP supports local stdio and local Streamable HTTP. Stdio is easiest for local coding agents. HTTP is better for clients that prefer a URL or for future hosted/tunneled use.

## Codex

Codex desktop app stdio in `~/.codex-app/config.toml`; Codex CLI stdio in `~/.codex/config.toml`:

```toml
[mcp_servers.driveops]
command = "/home/satyal/gdrive-mcp/.venv/bin/driveops-mcp"
args = ["stdio"]
cwd = "/home/satyal/gdrive-mcp"
startup_timeout_sec = 120
default_tools_approval_mode = "prompt"
```

The desktop app loads MCP servers when a thread starts. After editing config, start a new thread or restart the app/window. The first Drive tool call will trigger Google OAuth if no token exists, but the cleaner path is to run this once from a terminal first:

```bash
/home/satyal/gdrive-mcp/.venv/bin/driveops-mcp auth login
```

The command above uses the effective `DRIVEOPS_SCOPE_PROFILE` and defaults to
read-only. For a client configured with `DRIVEOPS_SCOPE_PROFILE=write`, you can
make the intended consent profile explicit:

```bash
/home/satyal/gdrive-mcp/.venv/bin/driveops-mcp auth login --profile write
```

Google OAuth scopes are stored in the token. Switching an existing setup from
read-only to write access therefore requires completing the write-profile
consent flow; changing the environment variable alone cannot broaden an
already-issued token.

### DriveOps credentials vs. host connectors

DriveOps uses the token configured by `DRIVEOPS_GOOGLE_TOKEN` (or its default
token path). A Google Drive connector built into the MCP host or assistant is a
separate application with its own OAuth grant. Authorizing DriveOps for writes
does not broaden that connector's scopes, and reconnecting the host connector
does not replace the DriveOps token. When diagnosing a permission error, check
which tool produced it and run `driveops-mcp auth status` against the same
environment used to start the DriveOps server.

## Browser Auth by OS

DriveOps opens Google OAuth lazily on the first Drive-accessing tool call. The same code path is used by Codex, Claude Code, Gemini CLI, Cursor, and other local stdio clients.

- Windows: uses the platform browser opener.
- WSL on Windows: prefers `wslview` when installed, then falls back to the Windows URL handler.
- macOS: uses Python's platform browser opener, which delegates to the system default browser.
- Linux desktop: uses Python's platform browser opener, usually through `xdg-open` or the configured `BROWSER`.
- Headless Linux, SSH, containers, and remote servers: automatic browser opening may not be possible. Use `driveops-mcp auth login` from a machine with browser access, set `DRIVEOPS_BROWSER`, or run a hosted HTTPS MCP deployment with a proper web OAuth flow.

HTTP:

```bash
driveops-mcp http --host 127.0.0.1 --port 8787
```

```toml
[mcp_servers.driveops]
url = "http://127.0.0.1:8787/mcp"
default_tools_approval_mode = "prompt"
```

## Claude Code

Stdio:

```bash
claude mcp add --transport stdio driveops -- /absolute/path/to/gdrive-mcp/.venv/bin/driveops-mcp stdio
```

HTTP:

```bash
claude mcp add --transport http driveops http://127.0.0.1:8787/mcp
```

## Gemini CLI

Add to `settings.json`:

```json
{
  "mcpServers": {
    "driveops": {
      "command": "/absolute/path/to/gdrive-mcp/.venv/bin/driveops-mcp",
      "args": ["stdio"],
      "cwd": "/absolute/path/to/gdrive-mcp",
      "trust": false
    }
  }
}
```

HTTP:

```json
{
  "mcpServers": {
    "driveops": {
      "httpUrl": "http://127.0.0.1:8787/mcp",
      "trust": false
    }
  }
}
```

## Cursor

Use Cursor's MCP settings and add either a stdio command:

```json
{
  "mcpServers": {
    "driveops": {
      "command": "/absolute/path/to/gdrive-mcp/.venv/bin/driveops-mcp",
      "args": ["stdio"]
    }
  }
}
```

or an HTTP server:

```json
{
  "mcpServers": {
    "driveops": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

## ChatGPT Custom MCP Apps

ChatGPT web custom MCP apps need a reachable HTTPS Streamable HTTP server or a
supported private tunnel. For a public single-owner deployment, use DriveOps
`oauth` mode and add ChatGPT's exact published OAuth callback URL to
`DRIVEOPS_OAUTH_ALLOWED_REDIRECT_URIS`. Connect the app to
`https://your-host.example/mcp`; OAuth discovery and dynamic registration are
served automatically.

The hosted process uses the server owner's pre-authorized Google account. It is
not safe to offer the same deployment to unrelated users. See
[Public single-owner deployment](public-deployment.md).
