# Client Setup

DriveOps MCP supports local stdio and local Streamable HTTP. Stdio is easiest for local coding agents. HTTP is better for clients that prefer a URL or for future hosted/tunneled use.

## Codex

Stdio in `~/.codex/config.toml`:

```toml
[mcp_servers.driveops]
command = "/absolute/path/to/gdrive-mcp/.venv/bin/driveops-mcp"
args = ["stdio"]
cwd = "/absolute/path/to/gdrive-mcp"
default_tools_approval_mode = "prompt"
```

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
