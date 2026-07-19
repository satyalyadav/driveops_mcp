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

The MVP is local-first. If you host it for ChatGPT or remote clients:

- put it behind HTTPS;
- isolate users and their OAuth tokens;
- add auth before the MCP endpoint;
- keep audit logs per user;
- restrict write scopes by default;
- rate-limit large folder reads and plan generation.
