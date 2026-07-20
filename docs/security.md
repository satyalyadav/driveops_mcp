# Security

DriveOps MCP is built around safe writes:

- read-only OAuth scopes by default;
- write tools require `DRIVEOPS_SCOPE_PROFILE=write`;
- all Drive mutations, including file and permission actions, require a stored `plan_id`;
- applying a plan requires the exact confirmation string from `driveops.preview_plan`;
- undo requires the exact undo confirmation string;
- every create, upload, rename, copy, move, trash/delete, permission, and undo event is written to SQLite;
- permanent-delete plans are labeled irreversible and cannot be undone;
- partially applied plans remain undoable when a later batch action fails;
- plan application and undo are atomically claimed so concurrent requests cannot execute the same plan twice;
- partial undo progress is stored step-by-step and safely skipped when an undo is retried;
- ZIP extraction blocks path traversal, symlinks, oversized expansion, and silent overwrite;
- downloads refuse to overwrite local files unless explicitly requested.
- OAuth tokens and the local audit database use owner-only permissions on POSIX systems.

## Secrets

Do not keep OAuth files in the repository. Store them under:

```text
~/.config/driveops-mcp/
```

or configure explicit paths:

```bash
export DRIVEOPS_GOOGLE_CLIENT_SECRET=/secure/path/client_secret.json
export DRIVEOPS_GOOGLE_TOKEN=/secure/path/token.json
```

If a token or client secret is accidentally shared, revoke it from Google Account security settings and rotate the OAuth client secret in Google Cloud.

## Prompt Injection

Drive file content is untrusted model input. A malicious document can tell an agent to ignore instructions or call tools. Keep write tools on approval mode in your MCP client, review each proposed plan, and do not use `Allow always` for DriveOps write tools.

## Hosted Deployments

Hosted mode is designed for one owner and one pre-authorized Google account. It
supports MCP OAuth for web clients and static Bearer authentication for clients
that can set headers. Public binding fails closed without authentication, and
remote write scope and server-filesystem paths are disabled by default.

This does not make the server multi-tenant. Do not share one deployment with
untrusted users: every authorized client acts as the same connected Google owner.
Multi-user hosting requires per-user Google OAuth, per-user plan/audit namespaces,
and an external transactional database.

See [Public single-owner deployment](public-deployment.md) for the threat model,
required secrets, persistent-state constraints, and deployment checklist.
