# Security

DriveOps MCP is built around safe writes:

- read-only OAuth scopes by default;
- write tools require `DRIVEOPS_SCOPE_PROFILE=write`;
- all Drive mutations, including file and permission actions, require a stored `plan_id`;
- applying a plan requires the exact confirmation string from `driveops.preview_plan`;
- undo requires the exact undo confirmation string;
- successful mutation and undo steps are checkpointed in SQLite before their audit event is appended;
- permanent deletes are labeled irreversible, cannot be undone, and must use a separate plan;
- partially applied plans remain undoable unless a target changed afterward;
- plan application and undo are atomically claimed so concurrent requests cannot execute the same plan twice;
- apply follows the previewed step order and refuses stale file or permission snapshots;
- undo progress is stored step-by-step, retries skip completed steps, and stale targets are left untouched;
- ZIP extraction blocks path traversal, symlinks, oversized expansion, and silent overwrite;
- downloads refuse to overwrite local files unless explicitly requested.
- OAuth tokens, databases, and the plan-encryption key use owner-only permissions on POSIX systems.

Plan payloads are encrypted with Fernet because they can contain inline upload
content and local paths. Local installs create `driveops.key` beside the audit
database. Hosted installs should set `DRIVEOPS_PLAN_ENCRYPTION_KEY` from a secret
manager and back up that key separately. Losing or rotating it without migration
makes existing plans unreadable. Audit-event metadata and OAuth client metadata
are not application-level encrypted, so the state volume and backups must still
be encrypted by the host.

The checkpoint removes most ambiguity after ordinary failures, but Google Drive
and SQLite do not share a transaction. A process crash in the small interval
between a Google API success and the local checkpoint can still require manual
inspection before retrying.

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
