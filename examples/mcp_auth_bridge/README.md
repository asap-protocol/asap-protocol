# MCP Auth Bridge reference example

Runnable demo of **Mode A** native MCP protection via `asap.mcp.auth.protect_server`:

- **`echo`** — public tool (`public_tools`); no Agent JWT required.
- **`secure_action`** — protected; requires a valid Agent JWT, matching `capabilities` claim, and an active registry grant.

Keys and identities are generated **at runtime** (nothing committed to the repo).

## Prerequisites

- Python 3.13+
- [uv](https://github.com/astral-sh/uv)

From the repository root:

```bash
uv sync
```

## Quick start (recommended)

The example **client** is self-contained: it spawns `server.py`, reads the minted demo Agent JWT from the **child** stderr, then calls `echo` and `secure_action`.

```bash
uv run python examples/mcp_auth_bridge/client.py
```

```bash
cd examples/mcp_auth_bridge
uv run python client.py
```

Do **not** pass a real JWT on the client CLI (`argv` leaks secrets). Each
server process mints its own keys, so a foreign token fails signature verification
(`bad_signature`).

The client does **not** read `ASAP_AGENT_JWT` from the shell (a stale export used
to skip stderr capture and cause confusing `bad_signature` failures). Use
`--invalid-jwt` only for deliberate negative tests of `secure_action`.

`ASAP_AGENT_JWT` remains meaningful only for the **server** middleware
(`allow_env_jwt_fallback=True`) when calling tools without `_meta` — see §3.

## Run the server alone

Useful for manual MCP clients, compliance probes, or reviewing the stderr banner:

```bash
uv run python examples/mcp_auth_bridge/server.py
```

On startup the server prints a **demo Agent JWT** and usage hints on **stderr** (stdout stays reserved for MCP JSON-RPC).

Help (does not start the stdio loop):

```bash
uv run python examples/mcp_auth_bridge/server.py --help
```

## Agent JWT flow

### 1. Host minting (production pattern)

In production the **host** signs Agent JWTs with its Ed25519 private key after the agent session is registered. This example inlines that flow in `server.py` using the same APIs as `tests/adapters/mcp/conftest.py`:

1. Register `HostIdentity` in `InMemoryHostStore`.
2. Register `AgentSession` in `InMemoryAgentStore`.
3. Mint with `create_agent_jwt(agent_sk, host_thumbprint=..., agent_id=..., aud=..., capabilities=[...])`.

The demo server prints one minted token on stderr for local inspection. The bundled `client.py` captures that token from the **same** child process automatically.

### 2. Pass JWT on `tools/call`

MCP clients should attach the token under `_meta` (must be the JWT minted by **that** server process):

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "secure_action",
    "arguments": {"action": "ping"},
    "_meta": {"asap_agent_jwt": "<token-from-this-server-stderr>"}
  }
}
```

`echo` ignores JWT even if present; `secure_action` rejects calls without a valid token (`asap:auth_required`).

### 3. Dev env fallback

When `MCPAuthConfig.allow_env_jwt_fallback=True` (enabled in this example for local docs only):

```bash
# Only valid when ASAP_AGENT_JWT is the JWT minted by *this* server process
# (e.g. injected into the same child env — not a paste from another terminal).
export ASAP_AGENT_JWT='<token-from-this-server-stderr>'
```

The middleware reads `ASAP_AGENT_JWT` when `_meta.asap_agent_jwt` is absent. **Do not enable in production.**

## Reviewer checklist

1. `uv run python examples/mcp_auth_bridge/server.py --help` → exit 0.
2. `uv run python examples/mcp_auth_bridge/client.py` (no JWT args) → `echo` + `secure_action` succeed.
3. `uv run python examples/mcp_auth_bridge/client.py --invalid-jwt` → `echo` succeeds; `secure_action` fails.
4. Start server alone; inspect demo JWT on stderr (do not paste into a second unrelated server).
5. Call `echo` without JWT → succeeds.
6. Call `secure_action` without JWT → `asap:auth_required`.
7. Call `secure_action` with `_meta.asap_agent_jwt` from **that** server's stderr (or `ASAP_AGENT_JWT` in the same process) → `executed: ...`.

## Compliance

The **`mcp-auth-bridge`** profile in [`asap-compliance`](../../asap-compliance/) is the **v2.5.0 release gate** for stdio MCP auth. It black-boxes this example server (subprocess `uv run python examples/mcp_auth_bridge/server.py`) and asserts:

| Check | What it verifies |
|-------|------------------|
| `auth_required` | Unauthenticated `secure_action` → `asap:auth_required` |
| `valid_jwt` | Valid Agent JWT on `secure_action` succeeds |
| `wrong_capability` | JWT without the mapped capability → `asap:capability_denied` |
| `constraint_violation` | Grant constraint breach → `asap:constraint_violation` |
| `manifest_alignment` | Manifest-declared tools/capabilities ⊆ registered MCP surface (MCP-DISC-003) |

There is no dedicated `python -m` CLI for this profile yet — use pytest or the programmatic one-liner below.

**Recommended gate** (from the repository root):

```bash
uv run pytest asap-compliance/tests/test_mcp_auth.py -v
```

**Programmatic one-liner:**

```bash
PYTHONPATH=asap-compliance:src uv run python -c "
from asap_compliance.harness import McpAuthComplianceConfig, validate_mcp_auth
import sys
result = validate_mcp_auth(McpAuthComplianceConfig())
for check in result.checks:
    if not check.passed:
        print(f'FAIL: {check.name}: {check.message}')
sys.exit(0 if result.passed else 1)
"
```

The subprocess driver sets `ASAP_MCP_COMPLIANCE=1` on the server automatically so probe JWTs (wrong capability, constraint action) are emitted on stderr — you do not need to export it manually.

Adapter semantics and error codes: [`docs/adapters/mcp-auth-bridge.md`](../../docs/adapters/mcp-auth-bridge.md).

## Related docs

- Adapter guide: [`docs/adapters/mcp-auth-bridge.md`](../../docs/adapters/mcp-auth-bridge.md)
- Mode B (MCP-over-ASAP envelope): `src/asap/examples/mcp_integration.py`
- `initialize` session-token carriage: not implemented in this stdio example
