# Security Guide

> Security guidance for implementing and operating ASAP protocol agents.

---

## Quick Reference

### Validation Constants

| Constant | Default Value | Description |
|----------|---------------|-------------|
| `MAX_ENVELOPE_AGE_SECONDS` | 300 (5 minutes) | Maximum age of envelope before rejection |
| `MAX_FUTURE_TOLERANCE_SECONDS` | 30 seconds | Maximum future timestamp offset allowed |
| `MAX_REQUEST_SIZE` | 10,485,760 bytes (10MB) | Maximum request body size |
| Default Rate Limit | 10/second;100/minute | Burst (10/s) + sustained (100/min) per sender |
| Nonce TTL | 600 seconds (10 minutes) | Time-to-live for nonce tracking (2x envelope age) |

### Security Features

| Feature | Status | Configuration |
|---------|--------|---------------|
| Timestamp Validation | Always Enabled | Automatic |
| Nonce Validation | Optional | `require_nonce=True` in `create_app()` |
| HTTPS Enforcement | Client-side (default) | `require_https=True` (default) |
| Rate Limiting | Enabled (default) | `rate_limit` parameter or `ASAP_RATE_LIMIT` env var |
| Request Size Limits | Enabled (default) | `max_request_size` parameter or `ASAP_MAX_REQUEST_SIZE` env var |

For **OpenAPI-backed workflow / automation connectors**, see
[Automation connector security](guides/automation-connector-security.md)
(secrets, TLS, webhooks, grants, MCP Path A notes).

---

## Overview

The ASAP protocol is designed with security as a foundational concern. For **v1.1 OAuth2, identity binding (Custom Claims), and trust limitations**, see [v1.1 Security Model](security/v1.1-security-model.md) (ADR-17).

This guide covers:

- **Authentication**: How agents verify identity
- **Request Signing**: Cryptographic integrity for messages
- **TLS/HTTPS**: Transport layer security requirements
- **Rate Limiting**: Per-sender request rate controls
- **Request Size Limits**: Protection against oversized payloads
- **Threat Model**: Common attack vectors and mitigations

---

## Authentication

ASAP supports multiple authentication schemes configured via the agent's manifest. The `AuthScheme` model defines the supported methods.

### Supported Authentication Schemes

The ASAP protocol validates authentication schemes at manifest creation time to ensure only supported schemes are used. The validation is enforced by the `Manifest` model and raises `UnsupportedAuthSchemeError` for invalid schemes.

| Scheme | Description | Use Case | Status |
|--------|-------------|----------|--------|
| `bearer` | Bearer token authentication (RFC 6750) | API keys, JWT tokens | ✅ Supported |
| `basic` | HTTP Basic authentication (RFC 7617) | Simple username/password | ✅ Supported |
| `oauth2` | OAuth 2.0 with authorization code or client credentials | Enterprise integrations | ✅ Supported (v1.1) |
| `hmac` | HMAC-based authentication | Message signing and verification | 🔜 Planned |
| `mtls` | Mutual TLS with client certificates | High-security environments | 🔜 Planned |
| `none` | No authentication (development only) | Local testing | ⚠️ Development only |

### Manifest Configuration

Authentication is declared in the agent manifest's `auth` field. The ASAP protocol automatically validates that all specified schemes are supported:

```python
from asap.models.entities import AuthScheme, Manifest
from asap.errors import UnsupportedAuthSchemeError
from asap.models.constants import SUPPORTED_AUTH_SCHEMES

# Valid: Bearer token authentication
auth = AuthScheme(schemes=["bearer"])
manifest = Manifest(
    id="urn:asap:agent:secure-agent",
    name="Secure Agent",
    version="1.0.0",
    description="Agent with Bearer token authentication",
    capabilities=capability,
    endpoints=endpoint,
    auth=auth
)

# Valid: Multiple supported schemes
auth = AuthScheme(schemes=["bearer", "basic"])
manifest = Manifest(
    id="urn:asap:agent:multi-auth-agent",
    name="Multi-Auth Agent",
    version="1.0.0",
    description="Agent with multiple authentication methods",
    capabilities=capability,
    endpoints=endpoint,
    auth=auth
)

# Invalid: Unsupported scheme raises UnsupportedAuthSchemeError
try:
    auth = AuthScheme(schemes=["oauth2"])  # v1.1: use OAuth2Config with create_app()
    manifest = Manifest(
        id="urn:asap:agent:invalid-agent",
        name="Invalid Agent",
        version="1.0.0",
        description="Agent with unsupported scheme",
        capabilities=capability,
        endpoints=endpoint,
        auth=auth
    )
except UnsupportedAuthSchemeError as e:
    print(f"Unsupported scheme: {e.scheme}")
    print(f"Supported schemes: {e.supported_schemes}")
```

#### Scheme Validation

The validation occurs automatically when creating a `Manifest` instance:

- **Supported Schemes**: `bearer`, `basic` (validated against `SUPPORTED_AUTH_SCHEMES`)
- **Validation Error**: Raises `UnsupportedAuthSchemeError` with code `asap:auth/unsupported_scheme`
- **Error Details**: Includes the unsupported scheme and list of supported schemes

#### Checking Supported Schemes

You can check which schemes are currently supported:

```python
from asap.models.constants import SUPPORTED_AUTH_SCHEMES

print(f"Supported schemes: {SUPPORTED_AUTH_SCHEMES}")
# Output: frozenset({'bearer', 'basic'})
```

### Required Headers

When making requests to authenticated agents, include the appropriate headers:

#### Bearer Token

Bearer token authentication is the most common method for API authentication. Include the token in the `Authorization` header:

```http
POST /asap HTTP/1.1
Host: agent.example.com
Content-Type: application/json
Authorization: Bearer <token>
```

**Python Example**:
```python
from asap.transport.client import ASAPClient

async with ASAPClient("https://api.example.com") as client:
    # Add Bearer token to client headers
    client._client.headers["Authorization"] = f"Bearer {my_token}"
    response = await client.send(envelope)
```

#### Basic Authentication

HTTP Basic authentication uses username and password encoded in base64:

```http
POST /asap HTTP/1.1
Host: agent.example.com
Content-Type: application/json
Authorization: Basic <base64-encoded-credentials>
```

**Python Example**:
```python
import base64
from asap.transport.client import ASAPClient

# Encode credentials
credentials = base64.b64encode(f"{username}:{password}".encode()).decode()

async with ASAPClient("https://api.example.com") as client:
    # Add Basic auth header
    client._client.headers["Authorization"] = f"Basic {credentials}"
    response = await client.send(envelope)
```

#### OAuth 2.0 (v1.1+)

OAuth 2.0 is supported in v1.1 with JWKS validation, OIDC discovery, and Custom Claims identity binding. See [v1.1 Security Model](security/v1.1-security-model.md) (ADR-17) for setup, trust limitations, and provider guides (Auth0, Keycloak, Azure AD).

- Authorization code flow (client-side)
- Client credentials flow (`OAuth2ClientCredentials`)
- Token validation via JWKS (OIDC discovery)
- Custom Claims or allowlist for agent identity binding

```http
POST /asap HTTP/1.1
Host: agent.example.com
Content-Type: application/json
Authorization: Bearer <access_token>
```

### Implementing Authentication

The ASAP protocol provides built-in authentication middleware for securing agent communication. Authentication is optional and configured via the manifest.

#### Quick Start with Bearer Tokens

```python
from asap.models.entities import Manifest, AuthScheme, Capability, Endpoint, Skill
from asap.transport.server import create_app

# 1. Configure authentication in manifest
manifest = Manifest(
    id="urn:asap:agent:secure-agent",
    name="Secure Agent",
    version="1.0.0",
    description="Agent with authentication",
    capabilities=Capability(
        asap_version="0.1",
        skills=[Skill(id="secure-task", description="Secure task processing")],
        state_persistence=False,
    ),
    endpoints=Endpoint(asap="https://api.example.com/asap"),
    auth=AuthScheme(schemes=["bearer"]),  # Enable Bearer token authentication
)

# 2. Implement token validation function
def validate_bearer_token(token: str) -> str | None:
    """Validate Bearer token and return agent ID if valid.
    
    Args:
        token: The Bearer token from Authorization header
        
    Returns:
        Agent ID (URN) if token is valid, None otherwise
    """
    # Example: Validate against database
    user = database.verify_token(token)
    if user and user.is_active:
        return user.agent_id  # e.g., "urn:asap:agent:client-123"
    
    return None

# 3. Create app with authentication
app = create_app(manifest, token_validator=validate_bearer_token)

# Run with uvicorn:
# uvicorn myapp:app --host 0.0.0.0 --port 8000
```

#### How It Works

1. **Manifest Declaration**: Set `auth=AuthScheme(schemes=["bearer"])` in manifest
2. **Token Validation**: Provide a `token_validator` function to `create_app()`
3. **Automatic Verification**: Middleware validates every request:
   - Checks for `Authorization: Bearer <token>` header
   - Calls your `token_validator` with the token
   - Returns HTTP 401 if token is invalid or missing
   - Verifies `envelope.sender` matches authenticated identity
   - Returns HTTP 403 if sender is spoofed

#### Custom Token Validation

Integrate with any authentication system (JWT, OAuth2, database, etc.):

```python
import jwt
from datetime import datetime

def validate_jwt_token(token: str) -> str | None:
    """Validate JWT token and extract agent ID."""
    try:
        # Decode and verify JWT
        payload = jwt.decode(
            token,
            key=PUBLIC_KEY,
            algorithms=["RS256"],
            options={"verify_exp": True}
        )
        
        # Verify custom claims
        if payload.get("iss") != "https://auth.example.com":
            return None
        
        # Extract agent ID
        return payload.get("sub")  # e.g., "urn:asap:agent:user-456"
        
    except jwt.InvalidTokenError:
        return None

app = create_app(manifest, token_validator=validate_jwt_token)
```

#### OAuth2 Integration

For OAuth2 workflows, implement token introspection:

```python
import httpx

async def validate_oauth2_token(token: str) -> str | None:
    """Validate OAuth2 token via introspection endpoint."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://auth.example.com/oauth/introspect",
            data={"token": token},
            auth=(CLIENT_ID, CLIENT_SECRET)
        )
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        if not data.get("active"):
            return None
        
        # Map OAuth2 subject to agent URN
        return f"urn:asap:agent:{data['sub']}"

app = create_app(manifest, token_validator=validate_oauth2_token)
```

#### Client-Side Authentication

When calling authenticated endpoints, include the Bearer token:

```python
from asap.transport.client import ASAPClient
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest

async with ASAPClient("https://api.example.com") as client:
    envelope = Envelope(
        sender="urn:asap:agent:client-123",
        recipient="urn:asap:agent:secure-agent",
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id="conv-1",
            skill_id="secure-task",
            input={"data": "sensitive-info"},
        ),
    )
    
    # Add authentication header to httpx client
    client._client.headers["Authorization"] = f"Bearer {my_token}"
    
    response = await client.send(envelope)
```

#### Security Best Practices

1. **Always use HTTPS** in production (client enforces this by default)
2. **Rotate tokens regularly** to limit exposure window
3. **Use short-lived tokens** (15-60 minutes) with refresh mechanism
4. **Validate sender identity** (middleware does this automatically)
5. **Log authentication failures** for security monitoring
6. **Rate limit authentication attempts** to prevent brute force

#### Disabling Authentication (Development Only)

```python
# Manifest without auth - authentication is skipped
manifest = Manifest(
    # ... other fields ...
    auth=None,  # No authentication required
)

app = create_app(manifest)  # No token_validator needed
```

⚠️ **Warning**: Only disable authentication for local development. Production deployments **must** use authentication.

---

## Request Signing

Request signing provides cryptographic proof of message origin and integrity, preventing tampering and ensuring non-repudiation.

### Signing Workflow

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│   Sender    │     │    Message      │     │  Recipient  │
└─────────────┘     └─────────────────┘     └─────────────┘
       │                    │                      │
       │  1. Create envelope                       │
       ├──────────►│                               │
       │                    │                      │
       │  2. Sign envelope with private key        │
       ├──────────►│                               │
       │                    │                      │
       │  3. Add signature to extensions           │
       ├──────────►│                               │
       │                    │                      │
       │           4. Send signed envelope         │
       ├───────────────────────────────────────────►
       │                    │                      │
       │           5. Verify signature with public key
       │                    │                      ◄─
       │           6. Process if valid             │
       │                    │                      ◄─
```

### Signature Format

Signatures are included in the envelope's `extensions` field:

```python
from datetime import datetime, timezone
from asap.models.envelope import Envelope

envelope = Envelope(
    asap_version="0.1",
    sender="urn:asap:agent:sender",
    recipient="urn:asap:agent:recipient",
    payload_type="task.request",
    payload={"skill_id": "research", "input": {}},
    extensions={
        "signature": {
            "algorithm": "Ed25519",
            "key_id": "key-2024-01",
            "value": "<base64-encoded-signature>",
            "signed_at": datetime.now(timezone.utc).isoformat()
        }
    }
)
```

### Signing Implementation

```python
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

def sign_envelope(envelope: Envelope, private_key: Ed25519PrivateKey) -> Envelope:
    """Sign an envelope and add signature to extensions."""
    # Canonical JSON representation (excluding signature field)
    canonical = envelope.model_dump(mode="json", exclude={"extensions"})
    message = json.dumps(canonical, sort_keys=True).encode()
    
    # Sign the message
    signature = private_key.sign(message)
    signature_b64 = base64.b64encode(signature).decode()
    
    # Add signature to extensions
    extensions = envelope.extensions or {}
    extensions["signature"] = {
        "algorithm": "Ed25519",
        "key_id": "key-2024-01",
        "value": signature_b64,
        "signed_at": datetime.now(timezone.utc).isoformat()
    }
    
    return envelope.model_copy(update={"extensions": extensions})
```

### Verification Steps

Recipients verify signatures before processing:

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

def verify_envelope(envelope: Envelope, public_key: Ed25519PublicKey) -> bool:
    """Verify envelope signature."""
    if not envelope.extensions or "signature" not in envelope.extensions:
        return False
    
    sig_data = envelope.extensions["signature"]
    signature = base64.b64decode(sig_data["value"])
    
    # Reconstruct canonical message
    canonical = envelope.model_dump(mode="json", exclude={"extensions"})
    message = json.dumps(canonical, sort_keys=True).encode()
    
    try:
        public_key.verify(signature, message)
        return True
    except InvalidSignature:
        return False
```

### Key Distribution

Public keys for signature verification should be distributed via:

1. **Manifest**: Include public key in the manifest's `signature` field
2. **Well-Known Endpoint**: `/.well-known/asap/keys.json`
3. **Key Server**: Centralized key management system

---

## TLS/HTTPS Requirements

All production ASAP deployments must use TLS 1.2 or higher.

### Minimum Requirements

| Requirement | Value |
|-------------|-------|
| TLS Version | 1.2+ (1.3 recommended) |
| Certificate | Valid, not self-signed |
| Cipher Suites | AEAD ciphers (AES-GCM, ChaCha20-Poly1305) |
| HSTS | Enabled with max-age ≥ 31536000 |

### Certificate Validation

The ASAP client validates server certificates by default:

```python
from asap.transport.client import ASAPClient

# Production: Certificate validation enabled (default)
async with ASAPClient(base_url="https://agent.example.com") as client:
    response = await client.send(envelope)

# Development only: Disable certificate validation (NOT FOR PRODUCTION)
async with ASAPClient(
    base_url="https://localhost:8443",
    verify_ssl=False  # DANGER: Only for local development
) as client:
    response = await client.send(envelope)
```

### Mutual TLS (mTLS)

For high-security deployments, configure mutual TLS:

```python
import httpx

# Client certificate configuration
client = httpx.AsyncClient(
    cert=("/path/to/client.crt", "/path/to/client.key"),
    verify="/path/to/ca-bundle.crt"
)
```

---

## Rate Limiting

Rate limiting protects ASAP agents from denial-of-service (DoS) attacks by limiting the number of requests per sender within a time window. The ASAP protocol server includes built-in rate limiting using per-sender tracking.

### Default Configuration

The default rate limit is **100 requests per minute** per sender. This can be configured via:

- **Environment variable**: `ASAP_RATE_LIMIT` (e.g., `"200/minute"`, `"10/second"`)
- **Application parameter**: `rate_limit` parameter in `create_app()`

### Configuration

```python
from asap.transport.server import create_app
from asap.models.entities import Manifest

# Configure via environment variable
# export ASAP_RATE_LIMIT="200/minute"

# Or configure programmatically
app = create_app(
    manifest,
    rate_limit="200/minute"  # 200 requests per minute per sender
)
```

### Rate Limit Format

The rate limit string follows the format: `<number>/<unit>` where unit can be:
- `second` or `sec`
- `minute` or `min`
- `hour` or `hr`
- `day`

Examples:
- `"10/second;100/minute"` - Burst (10/s) + sustained (100/min) - **default**
- `"100/minute"` - 100 requests per minute (no burst allowance)
- `"10/second"` - 10 requests per second
- `"1000/hour"` - 1000 requests per hour

### How It Works

1. **Sender Identification**: The rate limiter extracts the sender from the ASAP envelope (`envelope.sender`). If the envelope is not yet parsed, it falls back to the client IP address.

2. **Per-Sender Tracking**: Each sender (agent URN or IP) has an independent rate limit counter.

3. **Window-Based Limiting**: Uses a sliding window to track requests within the time period.

4. **Automatic Rejection**: When the limit is exceeded, the server returns HTTP 429 (Too Many Requests) with a JSON-RPC error response.

### Response Format

When rate limit is exceeded, the server returns:

```json
{
  "jsonrpc": "2.0",
  "id": "request-id",
  "error": {
    "code": -32000,
    "message": "Rate limit exceeded",
    "data": {
      "error": "Rate limit exceeded: 100 per 1 minute",
      "retry_after": 45
    }
  }
}
```

The response includes:
- **HTTP Status**: 429 (Too Many Requests)
- **Retry-After Header**: Number of seconds until the rate limit resets
- **JSON-RPC Error**: Standard error format with rate limit details

### Production Recommendations

1. **Adjust Limits Based on Workload**: 
   - High-throughput agents: `200-500/minute`
   - Interactive agents: `50-100/minute`
   - Resource-intensive agents: `10-50/minute`

2. **Use Distributed Storage**: For multi-instance deployments, set the Redis backend (requires `pip install 'asap-protocol[redis]'`):
   ```bash
   export ASAP_RATE_LIMIT_BACKEND=redis://localhost:6379/0
   ```
   The transport layer uses the `limits` library (`create_limiter()` in `asap.transport.rate_limit`).

3. **Implement Graduated Responses**: Consider implementing different limits for different sender types (trusted vs. untrusted).

### Example: Custom Rate Limit Configuration

```python
import os
from asap.transport.server import create_app

# Read from environment or use default (burst + sustained)
rate_limit = os.getenv("ASAP_RATE_LIMIT", "10/second;100/minute")

app = create_app(
    manifest,
    rate_limit=rate_limit
)
```

### Testing Rate Limits

To test rate limiting behavior:

```python
import asyncio
import httpx

async def test_rate_limit():
    """Test that rate limiting rejects excessive requests."""
    async with httpx.AsyncClient() as client:
        # Send 101 requests rapidly
        for i in range(101):
            response = await client.post(
                "http://localhost:8000/asap",
                json={
                    "jsonrpc": "2.0",
                    "method": "asap.send",
                    "params": {
                        "envelope": {
                            "asap_version": "0.1",
                            "sender": "urn:asap:agent:test-client",
                            "recipient": "urn:asap:agent:server",
                            "payload_type": "task.request",
                            "payload": {"test": "data"}
                        }
                    },
                    "id": f"test-{i}"
                }
            )
            
            if i < 100:
                assert response.status_code == 200
            else:
                assert response.status_code == 429
                assert "Rate limit exceeded" in response.json()["error"]["message"]

asyncio.run(test_rate_limit())
```

---

## Request Size Limits

Request size limits protect agents from memory exhaustion attacks by rejecting oversized payloads before they are fully processed. The ASAP protocol server enforces a maximum request size to prevent DoS attacks.

### Default Configuration

The default maximum request size is **10MB (10,485,760 bytes)**. This can be configured via:

- **Environment variable**: `ASAP_MAX_REQUEST_SIZE` (in bytes, e.g., `"5242880"` for 5MB)
- **Application parameter**: `max_request_size` parameter in `create_app()`

### Configuration

```python
from asap.transport.server import create_app
from asap.models.constants import MAX_REQUEST_SIZE

# Use default (10MB)
app = create_app(manifest)

# Configure via environment variable
# export ASAP_MAX_REQUEST_SIZE="5242880"  # 5MB

# Or configure programmatically
app = create_app(
    manifest,
    max_request_size=5 * 1024 * 1024  # 5MB in bytes
)
```

### How It Works

The server validates request size in two stages:

1. **Content-Length Header Check**: If the `Content-Length` header is present, the server checks it before reading the request body. This allows early rejection without consuming bandwidth.

2. **Actual Body Size Check**: After reading the request body, the server validates the actual size. This catches cases where the `Content-Length` header is missing or incorrect.

### Error Response

When a request exceeds the size limit, the server returns:

```json
{
  "jsonrpc": "2.0",
  "id": "request-id",
  "error": {
    "code": -32000,
    "message": "Request too large",
    "data": {
      "error": "Request size (15728640 bytes) exceeds maximum (10485760 bytes)"
    }
  }
}
```

The response includes:
- **HTTP Status**: 413 (Payload Too Large)
- **JSON-RPC Error**: Standard error format with size details

### Rationale

The 10MB default limit balances:
- **Functionality**: Allows large payloads for legitimate use cases (file uploads, batch operations)
- **Security**: Prevents memory exhaustion from malicious oversized requests
- **Performance**: Enables early rejection before full request processing

### Production Recommendations

1. **Adjust Based on Use Case**:
   - File processing agents: `50-100MB`
   - API gateway agents: `1-5MB`
   - Real-time agents: `1MB or less`

2. **Configure at Multiple Layers**:
   - **ASGI Server**: Set `--limit-max-requests` in uvicorn/gunicorn
   - **Reverse Proxy**: Configure nginx/traefik with `client_max_body_size`
   - **Application**: Use `max_request_size` parameter

3. **Monitor Size Violations**: Track rejected requests to identify potential attacks or legitimate needs for larger limits.

### Example: Multi-Layer Size Protection

```python
# Application level (ASAP server)
app = create_app(
    manifest,
    max_request_size=10 * 1024 * 1024  # 10MB
)

# Run with uvicorn size limit
# uvicorn app:app --limit-max-requests 10485760  # 10MB
```

```nginx
# nginx configuration
http {
    client_max_body_size 10M;
    
    server {
        location /asap {
            proxy_pass http://localhost:8000;
        }
    }
}
```

### Testing Size Limits

To test size limit enforcement:

```python
import httpx

def test_size_limit():
    """Test that oversized requests are rejected."""
    # Create a payload that exceeds 10MB
    large_payload = {"data": "x" * (11 * 1024 * 1024)}  # 11MB
    
    envelope = {
        "asap_version": "0.1",
        "sender": "urn:asap:agent:client",
        "recipient": "urn:asap:agent:server",
        "payload_type": "task.request",
        "payload": large_payload
    }
    
    rpc_request = {
        "jsonrpc": "2.0",
        "method": "asap.send",
        "params": {"envelope": envelope},
        "id": "test-1"
    }
    
    # Serialize to JSON
    import json
    request_json = json.dumps(rpc_request)
    request_bytes = request_json.encode("utf-8")
    
    # Send with Content-Length header
    response = httpx.post(
        "http://localhost:8000/asap",
        content=request_bytes,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(request_bytes))
        }
    )
    
    assert response.status_code == 413
    assert "exceeds maximum" in response.json()["error"]["data"]["error"]
```

---

## Handler Security

Handlers process incoming ASAP envelopes and must validate all inputs before use. Unvalidated payloads, URIs, or envelope fields can lead to path traversal, injection, or information disclosure.

### Input Validation Requirements

1. **Validate payload structure**: Parse payloads with Pydantic models (e.g. `TaskRequest`) so required fields and types are enforced.
2. **Validate URIs in parts**: Reject path traversal (`../`) and suspicious `file://` URIs in `FilePart` and similar fields. Use the built-in `FilePart` URI validator.
3. **Validate handler signature**: Handlers must accept `(envelope: Envelope, manifest: Manifest)` and return `Envelope` (sync) or `Awaitable[Envelope]` (async). Use `validate_handler()` when registering custom handlers.
4. **Do not trust envelope payload as raw dict**: Always parse into typed models and handle `ValidationError`.
5. **Sanitize data before logging**: Use `sanitize_for_logging()` for envelope or payload data in log messages.

### Handler Security Review Checklist

Before deploying a handler, verify:

- [ ] Payload is parsed with a Pydantic model (e.g. `TaskRequest(**envelope.payload)` inside try/except).
- [ ] All user-controlled strings (URIs, IDs, file paths) are validated or allowlisted.
- [ ] No path traversal possible: `FilePart` URIs use the built-in validator; custom file paths are normalized and checked against a base directory.
- [ ] Handler signature is `(envelope: Envelope, manifest: Manifest)` and return type is `Envelope` or `Awaitable[Envelope]`.
- [ ] Errors are logged server-side with full context; responses to clients are generic in production (no stack traces or internal details).
- [ ] No secrets or PII are logged; use `sanitize_for_logging()` when logging payloads or envelope content.

### Secure vs Insecure Handlers

**Insecure**: Raw payload, no URI validation, secrets in logs.

```python
def bad_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
    # BAD: No payload validation
    uri = envelope.payload.get("file_uri")  # type: ignore[union-attr]
    # BAD: Path traversal possible
    with open(uri.replace("file://", "")) as f:
        data = f.read()
    # BAD: Logging raw payload (may contain secrets)
    logger.info("request", payload=envelope.payload)
    return build_response(envelope, data)
```

**Secure**: Typed payload, validated URIs, sanitized logging.

```python
from asap.models.payloads import TaskRequest
from asap.models.parts import FilePart
from asap.observability import sanitize_for_logging

def good_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
    # GOOD: Parse and validate payload
    try:
        task_request = TaskRequest(**envelope.payload)
    except ValidationError as e:
        raise MalformedEnvelopeError(reason="Invalid TaskRequest", details={"errors": e.errors()})
    # GOOD: Use validated parts (FilePart validates URIs, rejects path traversal)
    for part in task_request.input.get("parts", []):
        if part.get("type") == "file":
            file_part = FilePart.model_validate(part)  # URI validated here
            # Process file_part.uri safely
    # GOOD: Sanitize before logging
    logger.info("request", payload=sanitize_for_logging(envelope.payload))
    return build_response(envelope, result)
```

### FilePart URI Validation

`FilePart` validates `uri` to block path traversal and risky `file://` usage:

- Rejects URIs containing `../` (path traversal).
- Rejects `file://` URIs that are not explicitly allowed (e.g. allowlisted base path).
- Allows `asap://`, `https://`, and `data:` URIs when they meet format checks.

See `src/asap/models/parts.py` for the implementation. Use `FilePart` for any user-supplied file reference so validation is applied consistently.

### Handler signature validation

Use `validate_handler(handler)` from `asap.transport.handlers` to ensure a handler has the required signature `(envelope: Envelope, manifest: Manifest)` before registering it. The registry calls this automatically on `register()` so invalid handlers are rejected at registration time.

```python
from asap.transport.handlers import HandlerRegistry, validate_handler

def my_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
    ...

registry = HandlerRegistry()
validate_handler(my_handler)  # Optional: fail fast before register
registry.register("task.request", my_handler)
```

### Handler sandboxing (optional)

Handlers run in the same process as the server and have the same privileges. For untrusted or third-party handlers, consider:

- Running them in a separate process or worker pool with restricted resources.
- Validating and sanitizing all payload inputs before use (see input validation above).
- Using a bounded executor for sync handlers to limit CPU time (see `HandlerRegistry(executor=...)`).
- Not exposing sensitive environment variables or files to handler code.

ASAP does not provide a sandbox; implement process or resource isolation at the deployment level if needed.

---

## Replay Attack Prevention

ASAP protocol prevents replay attacks through timestamp validation and optional nonce-based duplicate detection. This ensures that intercepted or maliciously replayed messages cannot be processed after their validity window expires.

### Timestamp Validation

Every ASAP envelope includes a `timestamp` field that is automatically validated by the server. The validation enforces two constraints:

1. **Maximum Age**: Envelopes older than 5 minutes (300 seconds) are rejected
2. **Future Tolerance**: Envelopes with timestamps more than 30 seconds in the future are rejected

These windows balance security (preventing old message replays) with practical network latency and clock synchronization differences.

#### Configuration

Timestamp validation is **always enabled** on the server. The validation constants can be imported:

```python
from asap.models.constants import (
    MAX_ENVELOPE_AGE_SECONDS,      # Default: 300 (5 minutes)
    MAX_FUTURE_TOLERANCE_SECONDS,  # Default: 30 seconds
)
```

#### How It Works

When an envelope is received:

1. The server extracts the `timestamp` field from the envelope
2. Calculates the age: `current_time - envelope.timestamp`
3. If age > `MAX_ENVELOPE_AGE_SECONDS`, raises `InvalidTimestampError`
4. Calculates future offset: `envelope.timestamp - current_time`
5. If future offset > `MAX_FUTURE_TOLERANCE_SECONDS`, raises `InvalidTimestampError`

#### Error Response

When timestamp validation fails, the server returns:

```json
{
  "jsonrpc": "2.0",
  "id": "request-id",
  "error": {
    "code": -32602,
    "message": "Invalid params",
    "data": {
      "error": "Invalid envelope timestamp",
      "code": "asap:protocol/invalid_timestamp",
      "message": "Envelope timestamp is too old: 600.0 seconds (max: 300 seconds)",
      "details": {
        "timestamp": "2026-01-27T22:43:12.008942+00:00",
        "age_seconds": 600.0,
        "envelope_id": "01KG0TJCY8D6NW3DHZCKYJGF5H",
        "max_age_seconds": 300
      }
    }
  }
}
```

### Nonce-Based Duplicate Detection

For additional protection against replay attacks, ASAP supports optional nonce-based duplicate detection. When enabled, the server tracks nonce values and rejects duplicate nonces within a time-to-live (TTL) window.

#### Enabling Nonce Validation

Nonce validation is **optional** and must be explicitly enabled:

```python
from asap.transport.server import create_app

app = create_app(
    manifest,
    require_nonce=True  # Enable nonce validation
)
```

When enabled, the server creates an `InMemoryNonceStore` that tracks nonce values with a 10-minute TTL.

#### Using Nonces in Envelopes

Include a nonce in the envelope's `extensions` field:

```python
from asap.models.envelope import Envelope
import secrets

envelope = Envelope(
    asap_version="0.1",
    sender="urn:asap:agent:client",
    recipient="urn:asap:agent:server",
    payload_type="task.request",
    payload=task_request.model_dump(),
    extensions={
        "nonce": secrets.token_urlsafe(32)  # Generate unique nonce
    }
)
```

#### How It Works

1. **No Nonce**: If the envelope has no nonce, validation passes (nonce is optional)
2. **First Use**: When a nonce is first seen, it's marked as used with a 10-minute TTL
3. **Duplicate Detection**: If the same nonce is seen again within the TTL window, `InvalidNonceError` is raised
4. **Expiration**: After the TTL expires, the nonce is removed from the store and can be used again

#### Error Response

When a duplicate nonce is detected:

```json
{
  "jsonrpc": "2.0",
  "id": "request-id",
  "error": {
    "code": -32602,
    "message": "Invalid params",
    "data": {
      "error": "Invalid envelope nonce",
      "code": "asap:protocol/invalid_nonce",
      "message": "Duplicate nonce detected: abc123...",
      "details": {
        "nonce": "abc123...",
        "envelope_id": "01KG0TJCY8D6NW3DHZCKYJGF5H"
      }
    }
  }
}
```

#### Custom Nonce Store

For production deployments with multiple server instances, implement a distributed nonce store. `NonceStore` is a one-method protocol built around the atomic `check_and_mark` — a separate `is_used`/`mark_used` pair was removed because it advertised a TOCTOU footgun (two callers could race between the check and the mark).

```python
from asap.transport.validators import NonceStore
import redis

class RedisNonceStore:
    """Redis-based nonce store for distributed deployments."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def check_and_mark(self, nonce: str, ttl_seconds: int) -> bool:
        """Atomically claim the nonce. Returns True if it was unused (now claimed), False if already seen."""
        # SETNX returns 1 when the key was set (first sighting) and 0 when it already existed.
        return bool(self.redis.set(f"nonce:{nonce}", "1", nx=True, ex=ttl_seconds))

# Use custom store
redis_client = redis.Redis(host='localhost', port=6379, db=0)
nonce_store = RedisNonceStore(redis_client)

# Pass to server handler (requires custom handler setup)
```

#### Custom Agent Store

LIFE-005 session sliding must not clobber a concurrent revoke or key rotation.
`AgentStore.save` is still the full-row persist for register / rotate / revoke.
`verify_agent_jwt` persists idle timeout only through `touch_if_current` — the
same atomic check-and-act idea as `NonceStore.check_and_mark`.

```python
from datetime import datetime
from asap.auth.identity import AgentSession, AgentStore, jwk_thumbprint_sha256

class SqlAgentStore:
    """Agent store whose verify-path touch is a single UPDATE ... WHERE."""

    async def touch_if_current(
        self,
        agent_id: str,
        expected_public_key: dict,
        last_used_at: datetime,
        *,
        expected_host_id: str,
    ) -> AgentSession | None:
        thumb = jwk_thumbprint_sha256(expected_public_key)
        # UPDATE agents SET last_used_at = :ts
        #  WHERE agent_id = :id AND status = 'active' AND host_id = :host
        #    AND rfc7638(public_key) = :thumb
        # RETURNING *  — or None if rowcount == 0
        ...
```

### Best Practices

1. **Always Use Timestamps**: Timestamp validation is automatic and always enabled
2. **Enable Nonces for High-Security**: Use nonce validation for sensitive operations
3. **Generate Strong Nonces**: Use cryptographically secure random generators (e.g., `secrets.token_urlsafe()`)
4. **Distributed Deployments**: Use a shared nonce store (Redis, database) for multi-instance deployments
5. **Monitor Rejections**: Track `InvalidTimestampError` and `InvalidNonceError` to detect potential attacks
6. **Host JWT polling**: `GET /asap/agent/status` performs a read-only `jti` replay check so polling can reuse one Host JWT, but any token already consumed by register, revoke, or rotate-key is rejected.

### Multi-instance JWT replay (v2.5.2)

Single-process deployments keep the default in-memory
`asap.auth.agent_jwt.JtiReplayCache`. Multi-worker or multi-instance hosts must
share replay state so a JWT `jti` consumed on one replica is rejected on every
other replica.

```python
from asap.auth.jti_replay_cache import RedisJtiReplayCache
from asap.transport.server import create_app

# Requires: pip install 'asap-protocol[redis]'
jti_cache = RedisJtiReplayCache.from_url("redis://localhost:6379/0")
app = create_app(manifest, identity_jti_cache=jti_cache, ...)
```

Constraints:

| Item | Behavior |
|------|----------|
| Default TTL | 90 seconds per `(partition_key, jti)` key |
| Key shape | `{prefix}:{partition_key}:{jti}` (default prefix `asap:jti-replay`) |
| Recording routes | Register / revoke / rotate-key call `check_and_record` |
| Status polling | `GET /asap/agent/status` uses `record_jti=False` (read-only `contains`) |
| Redis errors | **Propagate** to callers — not fail-soft (unlike web app rate limits) |
| MCP Auth Bridge | Pass the same instance via `MCPAuthConfig(jti_replay_cache=jti_cache)` |

Install the optional extra with `pip install 'asap-protocol[redis]'` (or
`uv sync --extra redis`). Pair this with `ASAP_RATE_LIMIT_BACKEND` and a shared
nonce store when running more than one worker. See
[migration — Redis JTI](migration.md#redis-backed-jti-replay-cache-209).

### Custom AgentStore (revocation permanence)

Injectable `AgentStore` implementations (`identity_agent_store`, MCP Auth
Bridge `agent_store`) must treat revocation as a hard kill switch. `save`
MUST atomically refuse to replace a `revoked` row with a non-revoked snapshot
and raise `RevokedAgentOverwriteError`. Do not re-get then save in the caller
— that is the same TOCTOU class as a split `is_used` / `mark_used` nonce API.
Call `raise_if_revoked_agent_overwrite(existing, incoming)` after an atomic
read and before the write, with no `await` between those steps.

```python
from asap.auth.identity import AgentSession, raise_if_revoked_agent_overwrite

class RedisAgentStore:
    async def save(self, agent: AgentSession) -> None:
        # One Redis round-trip (Lua / WATCH+MULTI). After the atomic read,
        # call raise_if_revoked_agent_overwrite before the write with no await
        # between those two steps — same as InMemoryAgentStore (dict check then assign).
        await self._compare_and_set_unless_revoked(agent)
```

`InMemoryAgentStore.save` already does this (dict check then assign, no await).
`verify_agent_jwt` slides idle timeout via ``touch_if_current`` (PR #323) and
never full-row ``save``s a verify-time snapshot. Rotate/reactivate/status
map ``RevokedAgentOverwriteError`` to a lifecycle HTTP error instead of 500.

---

## Operator REST APIs (v2.5.2)

`/usage`, `/sla`, and `/audit` are **operator REST surfaces** (not JSON-RPC).
They are mounted when the corresponding stores are passed to `create_app`, and
remain **unauthenticated by default** for local/operator ergonomics. The server
logs a startup warning when any of these routes are exposed without auth.

### Enable OAuth2 protection

```python
from asap.auth import OAuth2Config
from asap.transport.server import create_app

app = create_app(
    manifest,
    oauth2_config=OAuth2Config(jwks_uri="https://idp.example.com/jwks.json"),
    metering_storage=...,  # mounts /usage
    sla_storage=...,       # mounts /sla
    audit_store=...,       # mounts /audit
    require_operator_auth=True,
)
```

When `require_operator_auth=True`:

- Paths under `/usage`, `/sla`, and `/audit` require a Bearer JWT.
- Route dependency enforces scope ``asap:admin`` (``asap:execute`` alone → 403).
- Identity binding still applies (custom claim = `manifest.id`, or `sub` in
  `ASAP_AUTH_SUBJECT_MAP`) — same rule as `/asap`.
- `oauth2_config` is required or `create_app` raises `ValueError`.
- `OAuth2Config.required_scope` for `/asap` is **not** applied to operator
  routes; those enforce only ``asap:admin``.

Do **not** expose metering, SLA, or audit beyond localhost without
`require_operator_auth=True`. Full token examples and IdP pitfalls:
[migration — operator API auth](migration.md#opt-in-operator-api-auth-209) and
[Configuring Custom Claims](security/v1.1-security-model.md#configuring-custom-claims).

---

## HTTPS Enforcement

The ASAP client enforces HTTPS for production connections by default, preventing unencrypted communication that could expose sensitive data or be intercepted by attackers.

### Client-Side Enforcement

The `ASAPClient` validates URL schemes during initialization:

- **HTTPS URLs**: Always accepted
- **HTTP localhost**: Accepted with a warning (for development)
- **HTTP production**: Rejected with `ValueError` (security requirement)

#### Default Behavior

```python
from asap.transport.client import ASAPClient

# HTTPS: Works (production)
async with ASAPClient("https://api.example.com") as client:
    response = await client.send(envelope)

# HTTP localhost: Works with warning (development)
async with ASAPClient("http://localhost:8000") as client:
    # Logs warning: "Using HTTP for localhost connection. For production, use HTTPS."
    response = await client.send(envelope)

# HTTP production: Raises ValueError
try:
    client = ASAPClient("http://api.example.com")
except ValueError as e:
    # Error: "HTTPS is required for non-localhost connections..."
```

#### Override for Development

For local development or testing, you can disable HTTPS enforcement:

```python
# Development only: Disable HTTPS requirement
async with ASAPClient(
    "http://localhost:8000",
    require_https=False  # Override default
) as client:
    response = await client.send(envelope)
```

⚠️ **Warning**: Never use `require_https=False` in production. This disables a critical security check.

### Localhost Detection

The client automatically detects localhost connections using:

- Hostname: `localhost`
- IPv4: `127.0.0.1`
- IPv6: `::1`

These addresses are allowed to use HTTP with a warning, recognizing that local development often uses unencrypted connections.

### Production Recommendations

1. **Always Use HTTPS**: All production endpoints must use HTTPS
2. **Valid Certificates**: Use valid SSL certificates (not self-signed)
3. **TLS 1.2+**: Ensure servers support TLS 1.2 or higher
4. **Certificate Validation**: Never disable certificate validation in production
5. **HSTS**: Enable HTTP Strict Transport Security headers on servers

### Server Configuration

While the client enforces HTTPS, servers should also be configured to:

1. **Redirect HTTP to HTTPS**: Automatically redirect all HTTP requests to HTTPS
2. **HSTS Headers**: Include `Strict-Transport-Security` header
3. **Valid Certificates**: Use certificates from trusted Certificate Authorities

Example nginx configuration:

```nginx
server {
    listen 80;
    server_name api.example.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.example.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    
    location /asap {
        proxy_pass http://localhost:8000;
    }
}
```

---

## Threat Model

### Attack Vectors and Mitigations

| Threat | Attack Vector | Mitigation |
|--------|---------------|------------|
| **Eavesdropping** | Network interception | TLS encryption |
| **Tampering** | Message modification | Request signing |
| **Spoofing** | Identity impersonation | Authentication + signing |
| **Replay** | Message replay attacks | Timestamps + nonces |
| **Injection** | Malicious payload content | Input validation |
| **DoS** | Resource exhaustion | Rate limiting |
| **Man-in-the-Middle** | TLS interception | Certificate pinning |

### Replay Attack Prevention

ASAP uses automatic timestamp validation and optional nonce-based duplicate detection to prevent replay attacks. See the [Replay Attack Prevention](#replay-attack-prevention) section above for detailed documentation.

### Rate Limiting

The ASAP protocol server includes built-in rate limiting (see [Rate Limiting](#rate-limiting) section above). For custom implementations, you can integrate with external rate limiting services:

```python
# Example: Custom rate limiting middleware
from fastapi import Request, HTTPException
import redis

redis_client = redis.Redis(host='localhost', port=6379, db=0)

async def custom_rate_limit(request: Request):
    """Custom rate limiting using Redis."""
    sender = _get_sender_from_envelope(request)
    key = f"rate_limit:{sender}"
    
    # Use Redis INCR with expiration
    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, 60)  # 60 second window
    
    if count > 100:  # 100 requests per minute
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
```

For most use cases, the built-in rate limiting is recommended (see [Rate Limiting](#rate-limiting) section).

### Input Validation

Always validate payload content before processing:

```python
from pydantic import ValidationError
from asap.models.payloads import TaskRequest

def validate_payload(envelope: Envelope) -> TaskRequest:
    """Validate and parse TaskRequest payload."""
    try:
        return TaskRequest(**envelope.payload)
    except ValidationError as e:
        raise MalformedEnvelopeError(
            reason="Invalid TaskRequest payload",
            details={"validation_errors": e.errors()}
        )
```

---

## Security Checklist

### Production Deployment

- [ ] TLS 1.2+ enabled on all endpoints
- [ ] Valid SSL certificates (not self-signed)
- [ ] HSTS headers configured
- [ ] Authentication required for all endpoints
- [ ] Operator REST (`/usage`, `/sla`, `/audit`): `require_operator_auth=True` when exposed beyond localhost
- [ ] Multi-worker: shared `RedisJtiReplayCache` via `identity_jti_cache` (+ MCP `jti_replay_cache` if used)
- [ ] Rate limiting enabled and configured appropriately (`ASAP_RATE_LIMIT_BACKEND` for multi-instance)
- [ ] Request size limits configured (default: 10MB)
- [ ] Request signing implemented
- [ ] Audit logging enabled
- [ ] Secrets stored in environment variables
- [ ] Input validation on all payloads (no unknown `TaskRequest.config` / `CommonMetadata` keys)
- [ ] Certificate rotation process defined

### Development Environment

- [ ] Use `localhost` or private networks only
- [ ] Never use production credentials
- [ ] Clear separation from production data
- [ ] Self-signed certificates marked as development-only

---

## Related Documentation

- [Error Handling](error-handling.md) - ASAP error taxonomy
- [Transport](transport.md) - HTTP/JSON-RPC binding details
- [Observability](observability.md) - Logging and tracing
- [Automation connector security](guides/automation-connector-security.md) - OpenAPI workflow connectors
- [MCP Auth Bridge](adapters/mcp-auth-bridge.md) - Mode A JWT + grants on stdio MCP
