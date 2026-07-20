# Public single-owner deployment

DriveOps can be exposed as a secure single-owner remote MCP server. The hosted
process uses one pre-authorized Google account; MCP OAuth controls which remote
client may use that account. This is not a multi-tenant account-linking service.

## Security model

Public HTTP fails closed. A non-loopback listener or `DRIVEOPS_PUBLIC_URL`
requires one of these modes:

- `oauth`: OAuth authorization-code flow with PKCE, dynamic client registration,
  an owner approval page, exact callback allowlisting, short-lived access tokens,
  rotating refresh tokens, and revocation. Use this for web MCP clients.
- `token`: one static Bearer token. Use this only for clients that can configure
  an `Authorization` header.

OAuth authorization codes, access tokens, and refresh tokens are stored only as
SHA-256 hashes. Registered OAuth client metadata can include client secrets, so
the persistent state volume must be private and encrypted at rest.

Remote mode also:

- rejects unexpected `Host` and browser `Origin` headers on `/mcp`;
- limits request bodies to 2 MB by default;
- rate-limits each directly connected peer to 120 requests per minute per process;
- adds HSTS, CSP, clickjacking, MIME-sniffing, and referrer headers;
- attributes Drive write audit events to the authenticated OAuth/static client;
- uses stateless JSON MCP responses by default;
- disables tool access to paths on the server filesystem;
- refuses the Google write profile unless `DRIVEOPS_ALLOW_REMOTE_WRITE=1` is
  explicitly set.

Edge rate limiting, firewall rules, TLS, encrypted backups, and monitoring remain
the hosting operator's responsibility.

## Prepare the Google token

Authenticate locally first, using the default read-only profile:

```bash
driveops-mcp auth login --profile readonly
```

The resulting `token.json` contains a Google refresh token. On a host, either
mount it as an owner-readable secret file and set `DRIVEOPS_GOOGLE_TOKEN`, or
inject its complete JSON value as `DRIVEOPS_GOOGLE_TOKEN_JSON`. Do not include
the desktop OAuth client-secret file in a public deployment artifact.

If the injected refresh token is rejected, the hosted service fails instead of
opening an interactive browser. Reauthenticate locally and rotate the secret.

## OAuth mode for web clients

Configure the public origin and the exact callback URLs published by the clients
you intend to connect:

```bash
export DRIVEOPS_PUBLIC_URL=https://mcp.example.com
export DRIVEOPS_MCP_AUTH_MODE=oauth
export DRIVEOPS_OAUTH_ACCESS_KEY=replace-with-a-random-32-byte-or-longer-secret
export DRIVEOPS_OAUTH_ALLOWED_REDIRECT_URIS=https://client.example/oauth/callback
export DRIVEOPS_STATE_DIR=/persistent/driveops
driveops-mcp http --host 0.0.0.0 --port 8787
```

Generate the owner access key with a cryptographically secure secret generator,
for example `openssl rand -base64 32`, and store it in the host's secret manager.
When a client opens the DriveOps approval page, verify the displayed client and
callback before entering the key.

Multiple exact callbacks are comma-separated. Redirects must use HTTPS, except
loopback callbacks used for local testing. The MCP URL is:

```text
https://mcp.example.com/mcp
```

OAuth discovery endpoints are published automatically, including RFC 9728
protected-resource metadata, authorization-server metadata, dynamic registration,
token, and revocation endpoints.

## Static Bearer mode

For an MCP client that supports custom request headers:

```bash
export DRIVEOPS_PUBLIC_URL=https://mcp.example.com
export DRIVEOPS_MCP_AUTH_MODE=token
export DRIVEOPS_MCP_AUTH_TOKEN=replace-with-a-random-32-byte-or-longer-secret
export DRIVEOPS_STATE_DIR=/persistent/driveops
driveops-mcp http --host 0.0.0.0 --port 8787
```

Send `Authorization: Bearer <token>` on every `/mcp` request. Rotate the token if
it is exposed. Static token mode does not publish an interactive authorization
flow and therefore is generally unsuitable for web connector UIs.

The HTTP command honors a platform-provided `PORT` environment variable.
`/healthz` is a liveness endpoint and `/readyz` reports whether Google
credentials are configured. Neither endpoint performs a Google API call or
reveals credential contents.

## Persistence and scaling

`driveops.db` contains plans and audit events. `oauth.db` contains registered
clients, hashed tokens, and OAuth security events. Both must survive restarts.

The current persistence layer is SQLite. Run exactly one application instance on
a durable local volume and include both databases in encrypted backups. Do not
place SQLite on object-storage FUSE, run multiple replicas against one file, or
deploy to a filesystem that disappears on restart. A horizontally scaled or
scale-to-zero platform requires replacing these stores with a transactional
managed database before increasing the instance count.

## Operational checklist

- Terminate TLS at a trusted tunnel, load balancer, or reverse proxy.
- Keep `DRIVEOPS_SCOPE_PROFILE=readonly` initially.
- Set `DRIVEOPS_ALLOWED_HOSTS` for additional proxy hostnames, if necessary.
- Set `DRIVEOPS_ALLOWED_ORIGINS` only for browser origins that directly call MCP.
- Keep the process at one worker/instance while using SQLite.
- Put state on a private persistent volume with encrypted backups.
- Configure edge request limits in addition to the in-process limiter.
- Monitor authentication failures, rate-limit responses, Google API errors, and
  disk usage.
- Test revocation and secret rotation before enabling write scope.
- Never use `--unsafe-no-auth` on an internet-reachable endpoint.

The `--allow-local-file-access` switch re-enables server-side file paths for
downloads, extraction, and uploads. It should remain off for public deployments.
