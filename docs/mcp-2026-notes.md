# MCP 2026 Release-Candidate Notes

DriveOps MCP targets the MCP `2026-07-28` release candidate using `mcp[cli]==2.0.0a3`.

Design choices:

- no auth or Drive API calls happen at import time;
- Streamable HTTP runs with `stateless_http=True`;
- cross-call state is explicit through `plan_id`;
- long-running future work should use task handles rather than hidden sessions;
- schemas are designed to be JSON Schema 2020-12 compatible;
- server instructions explain the plan-preview-apply workflow.

The SDK v2 line is prerelease. Keep the dependency pinned exactly until the final SDK lands, then run the MCP contract tests before bumping.

Expected follow-up after the final spec:

- verify `server/discover` behavior directly against the final SDK;
- add official Tasks extension support for long folder scans if exposed by the SDK;
- add MCP Apps UI only after host support is practical for target clients.
