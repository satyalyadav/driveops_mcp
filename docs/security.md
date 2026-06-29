# Security

DriveOps MCP is built around safe writes:

- read-only OAuth scopes by default;
- write tools require `DRIVEOPS_SCOPE_PROFILE=write`;
- Drive mutations require a stored `plan_id`;
- applying a plan requires the exact confirmation string from `driveops.preview_plan`;
- undo requires the exact undo confirmation string;
- every create/move/undo event is written to SQLite.

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
