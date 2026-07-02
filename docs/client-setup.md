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

ChatGPT web custom MCP apps need a publicly reachable HTTPS Streamable HTTP server. For MVP use, run DriveOps locally and expose it through a trusted tunnel only for your own testing, or deploy it behind HTTPS. Do not expose the server publicly without proper OAuth, user isolation, and rate limiting.
