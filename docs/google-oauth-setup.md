# Durable Google OAuth Setup

DriveOps uses Google OAuth refresh tokens so it can reconnect after the
short-lived access token expires. The setup below avoids the most common failure:
a token that works initially and is rejected about seven days later.

## 1. Create the Google Cloud project

In Google Cloud Console:

1. Create or select a project.
2. Enable the **Google Drive API** and **Google Sheets API**.
3. Open **Google Auth Platform** and configure the app name and support email.
4. Under **Audience**, choose the appropriate user type:
   - **Internal** for users in a single Google Workspace or Cloud Identity
     organization.
   - **External** for personal Google accounts or users outside one organization.
5. If the app is External, change its publishing status from **Testing** to
   **In production** before relying on it beyond initial setup.

> [!IMPORTANT]
> Google expires refresh tokens for External apps left in Testing after seven
> days. Re-running login only starts another seven-day cycle. Retrying a rejected
> token cannot repair it.

An unverified External app can be used for personal use by fewer than 100 known
users, but Google shows an unverified-app warning and applies a lifetime 100-user
cap. A broadly distributed, maintainer-owned OAuth client that requests DriveOps'
restricted Drive scopes needs Google's OAuth verification and may require a
security assessment. For that reason, the repository uses bring-your-own Google
Cloud credentials by default.

## 2. Declare scopes and create a desktop client

Under **Data Access**, declare the scopes for the profile you will use.

Read-only profile:

```text
https://www.googleapis.com/auth/drive.metadata.readonly
https://www.googleapis.com/auth/drive.readonly
https://www.googleapis.com/auth/spreadsheets.readonly
```

Write profile:

```text
https://www.googleapis.com/auth/drive
https://www.googleapis.com/auth/spreadsheets
```

Then open **Clients**, create an OAuth client of type **Desktop app**, download
its JSON file, and save it as:

```text
~/.config/driveops-mcp/client_secret.json
```

Do not commit the downloaded client file or the generated token to Git.

## 3. Use one explicit profile and token path

The terminal command and MCP host must use the same environment. For write
access, for example:

```bash
export DRIVEOPS_SCOPE_PROFILE=write
export DRIVEOPS_GOOGLE_TOKEN="$HOME/.config/driveops-mcp/token-write.json"
driveops-mcp auth login --profile write
driveops-mcp auth check --profile write
```

A successful check includes:

```json
{
  "credentials_verified_online": true,
  "check_status": "ok"
}
```

Copy those same environment values into the MCP client's server configuration.
MCP-specific environment settings are not automatically inherited by commands
you run in a separate terminal.

## Troubleshooting

### `invalid_grant` or “expired or revoked” after about seven days

The Google OAuth app is probably External and still in Testing. Change **Google
Auth Platform > Audience > Publishing status** to **In production**, then mint one
new token:

```bash
driveops-mcp auth login --profile write --force
driveops-mcp auth check --profile write
```

Use `readonly` in both commands if that is your configured profile.

### The terminal succeeds but the MCP server still fails

Compare `token_path`, `profile`, and `google_cloud_project_id` from:

```bash
driveops-mcp auth status
```

against the `DRIVEOPS_GOOGLE_TOKEN`, `DRIVEOPS_SCOPE_PROFILE`, and
`DRIVEOPS_GOOGLE_CLIENT_SECRET` values in the MCP host configuration. Restart the
host or start a new thread after changing its MCP configuration.

### A refresh message says it is retrying

DriveOps retries only transient transport failures such as DNS or network
errors. It does not retry Google's permanent token rejection. If all three
transport attempts fail, the token file is kept so a later invocation can retry
after connectivity returns.
