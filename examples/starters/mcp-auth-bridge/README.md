# MCP Auth Bridge starter

Thin wrapper around the Mode A native MCP protection demo
(`asap.mcp.auth.protect_server`). The parent client spawns the demo
server, mints keys at runtime, and exercises `echo` (public) plus
`secure_action` (Agent JWT via `_meta`).

## Prerequisites

- Python 3.13+
- `uv`

```bash
uv sync
```

## Smoke

From the repository root:

```bash
uv run python examples/starters/mcp-auth-bridge/run.py
```

## Security

- Do **not** pass a real JWT on the CLI (`argv` leaks secrets).
- Do **not** enable `allow_env_jwt_fallback` in production (parent example enables it for local docs only).
- Keys and demo JWTs are minted in the child process; a token from another terminal will fail signature checks.

## Related

- Parent: [`examples/mcp_auth_bridge/`](../../mcp_auth_bridge/)
- Adapter guide: [`docs/adapters/mcp-auth-bridge.md`](../../../docs/adapters/mcp-auth-bridge.md)
- Starters index: [`../README.md`](../README.md)
