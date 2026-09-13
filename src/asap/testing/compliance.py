"""Compliance Harness v2 for ASAP protocol.

Runs validation checks covering identity, streaming, errors,
versioning, batch, and audit categories. Use ``run_compliance_harness_v2``
with an ASGI app (``httpx.ASGITransport``) or ``run_compliance_harness_v2_from_url``
with an HTTP(S) base URL for a remote agent.

Example::

    from asap.testing.compliance import run_compliance_harness_v2

    report = await run_compliance_harness_v2(app)
    print(report.to_json())
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx
from httpx import ASGITransport, AsyncClient, Timeout
from pydantic import BaseModel, ConfigDict, Field


class CheckResult(BaseModel):
    """Result of a single compliance check."""

    model_config = ConfigDict(frozen=True)

    name: str
    category: str
    passed: bool
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ComplianceReport(BaseModel):
    """Full compliance harness v2 report."""

    model_config = ConfigDict(frozen=True)

    version: str = "2.0"
    timestamp: datetime
    categories_run: list[str]
    checks: list[CheckResult]
    score: float
    summary: str

    def to_json(self) -> str:
        """Export report as JSON string."""
        return self.model_dump_json(indent=2)


# ---------------------------------------------------------------------------
# Category check functions
# ---------------------------------------------------------------------------

_DUMMY_ENVELOPE: dict[str, Any] = {
    "sender": "urn:asap:agent:compliance-test",
    "recipient": "urn:asap:agent:server",
    "payload_type": "task.request",
    "payload": {},
}

_RPC_WRAP: dict[str, Any] = {
    "jsonrpc": "2.0",
    "method": "asap.send",
    "params": {"envelope": _DUMMY_ENVELOPE},
    "id": "compliance-1",
}


async def check_identity(client: AsyncClient) -> list[CheckResult]:
    """Check agent identity and capability endpoints exist."""
    results: list[CheckResult] = []

    for name, method, path in [
        ("identity_register_endpoint", "POST", "/asap/agent/register"),
        ("identity_capability_list", "GET", "/asap/capability/list"),
    ]:
        try:
            r = await client.request(method, path)
            results.append(
                CheckResult(
                    name=name,
                    category="identity",
                    passed=r.status_code in (200, 401, 403),
                    message=f"{method} {path} returned {r.status_code}",
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(name=name, category="identity", passed=False, message=str(exc))
            )

    return results


async def check_streaming(client: AsyncClient) -> list[CheckResult]:
    """Check SSE streaming endpoint."""
    results: list[CheckResult] = []

    try:
        r = await client.post("/asap/stream", json=_RPC_WRAP)
        ct = r.headers.get("content-type", "")
        is_sse = "text/event-stream" in ct
        results.append(
            CheckResult(
                name="streaming_sse_endpoint",
                category="streaming",
                passed=is_sse or r.status_code == 200,
                message=f"POST /asap/stream content-type: {ct}",
            )
        )
    except Exception as exc:
        results.append(
            CheckResult(
                name="streaming_sse_endpoint", category="streaming", passed=False, message=str(exc)
            )
        )

    return results


async def check_errors(client: AsyncClient) -> list[CheckResult]:
    """Check error taxonomy compliance."""
    results: list[CheckResult] = []

    try:
        r = await client.post(
            "/asap",
            json={"jsonrpc": "2.0", "method": "nonexistent.method", "params": {}, "id": "e1"},
        )
        data: dict[str, Any] = r.json()
        code = data.get("error", {}).get("code")
        results.append(
            CheckResult(
                name="error_method_not_found",
                category="errors",
                passed=code == -32601,
                message=f"Method not found error code: {code}",
            )
        )
    except Exception as exc:
        results.append(
            CheckResult(
                name="error_method_not_found", category="errors", passed=False, message=str(exc)
            )
        )

    try:
        r = await client.post(
            "/asap", content=b"not json", headers={"content-type": "application/json"}
        )
        data = r.json()
        code = data.get("error", {}).get("code")
        results.append(
            CheckResult(
                name="error_parse_error",
                category="errors",
                passed=code == -32700,
                message=f"Parse error code: {code}",
            )
        )
    except Exception as exc:
        results.append(
            CheckResult(name="error_parse_error", category="errors", passed=False, message=str(exc))
        )

    try:
        r = await client.post(
            "/asap",
            json={
                "jsonrpc": "2.0",
                "method": "asap.send",
                "params": {"envelope": {"sender": "x", "recipient": "y", "payload_type": "bad"}},
                "id": "e3",
            },
        )
        data = r.json()
        results.append(
            CheckResult(
                name="error_data_structured",
                category="errors",
                passed="error" in data,
                message="Error response has structured data",
            )
        )
    except Exception as exc:
        results.append(
            CheckResult(
                name="error_data_structured", category="errors", passed=False, message=str(exc)
            )
        )

    return results


async def check_versioning(client: AsyncClient) -> list[CheckResult]:
    """Check ASAP-Version header negotiation."""
    results: list[CheckResult] = []

    try:
        r = await client.post("/asap", json=_RPC_WRAP, headers={"ASAP-Version": "2.2"})
        ver = r.headers.get("asap-version", "")
        results.append(
            CheckResult(
                name="versioning_header_present",
                category="versioning",
                passed=ver != "",
                message=f"Response ASAP-Version: {ver}",
            )
        )
    except Exception as exc:
        results.append(
            CheckResult(
                name="versioning_header_present",
                category="versioning",
                passed=False,
                message=str(exc),
            )
        )

    try:
        r = await client.post(
            "/asap",
            json={"jsonrpc": "2.0", "method": "asap.send", "params": {"envelope": {}}, "id": "v2"},
            headers={"ASAP-Version": "99.0"},
        )
        data: dict[str, Any] = r.json()
        code = data.get("error", {}).get("code")
        results.append(
            CheckResult(
                name="versioning_incompatible_rejected",
                category="versioning",
                passed=code == -32000,
                message=f"Incompatible version error code: {code}",
            )
        )
    except Exception as exc:
        results.append(
            CheckResult(
                name="versioning_incompatible_rejected",
                category="versioning",
                passed=False,
                message=str(exc),
            )
        )

    return results


async def check_batch(client: AsyncClient) -> list[CheckResult]:
    """Check JSON-RPC batch operations."""
    results: list[CheckResult] = []

    try:
        batch_body = [
            {**_RPC_WRAP, "id": "b1"},
            {**_RPC_WRAP, "id": "b2"},
        ]
        r = await client.post("/asap", json=batch_body)
        data: Any = r.json()
        results.append(
            CheckResult(
                name="batch_array_response",
                category="batch",
                passed=isinstance(data, list) and len(data) == 2,
                message=f"Batch returned {'array' if isinstance(data, list) else type(data).__name__}",
            )
        )
    except Exception as exc:
        results.append(
            CheckResult(
                name="batch_array_response", category="batch", passed=False, message=str(exc)
            )
        )

    try:
        r = await client.post("/asap", json=[])
        data = r.json()
        results.append(
            CheckResult(
                name="batch_empty_rejected",
                category="batch",
                passed="error" in data,
                message="Empty batch correctly rejected" if "error" in data else "Not rejected",
            )
        )
    except Exception as exc:
        results.append(
            CheckResult(
                name="batch_empty_rejected", category="batch", passed=False, message=str(exc)
            )
        )

    return results


async def check_audit(client: AsyncClient) -> list[CheckResult]:
    """Check audit log endpoint."""
    results: list[CheckResult] = []

    try:
        r = await client.get("/audit")
        results.append(
            CheckResult(
                name="audit_endpoint_exists",
                category="audit",
                passed=r.status_code in (200, 404),
                message=f"GET /audit returned {r.status_code}",
            )
        )
        if r.status_code == 200:
            data: dict[str, Any] = r.json()
            results.append(
                CheckResult(
                    name="audit_response_format",
                    category="audit",
                    passed="entries" in data,
                    message="Audit response has entries field"
                    if "entries" in data
                    else "Missing entries field",
                )
            )
    except Exception as exc:
        results.append(
            CheckResult(
                name="audit_endpoint_exists", category="audit", passed=False, message=str(exc)
            )
        )

    return results


# ---------------------------------------------------------------------------
# Registry & runner
# ---------------------------------------------------------------------------

CATEGORY_CHECKS: dict[str, Callable[[AsyncClient], Awaitable[list[CheckResult]]]] = {
    "identity": check_identity,
    "streaming": check_streaming,
    "errors": check_errors,
    "versioning": check_versioning,
    "batch": check_batch,
    "audit": check_audit,
}

ALL_CATEGORIES: list[str] = list(CATEGORY_CHECKS.keys())


async def run_compliance_harness_with_client(
    client: AsyncClient,
    *,
    categories: list[str] | None = None,
) -> ComplianceReport:
    """Run harness checks using an existing ``httpx.AsyncClient`` (any transport/base_url)."""
    cats = categories or list(ALL_CATEGORIES)
    all_checks: list[CheckResult] = []

    for cat in cats:
        checker = CATEGORY_CHECKS.get(cat)
        if checker is not None:
            check_results = await checker(client)
            all_checks.extend(check_results)

    total = len(all_checks)
    passed = sum(1 for c in all_checks if c.passed)
    score = passed / total if total > 0 else 0.0

    return ComplianceReport(
        timestamp=datetime.now(timezone.utc),
        categories_run=cats,
        checks=all_checks,
        score=score,
        summary=f"{passed}/{total} checks passed ({score:.0%})",
    )


async def run_compliance_harness_v2(
    app: Any,
    *,
    categories: list[str] | None = None,
) -> ComplianceReport:
    """Run compliance harness v2 against an ASGI application.

    Args:
        app: ASGI application (e.g. FastAPI app from ``create_app``).
        categories: Categories to check. Defaults to all.

    Returns:
        ComplianceReport with all check results and score.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await run_compliance_harness_with_client(client, categories=categories)


async def run_compliance_harness_v2_from_url(
    base_url: str,
    *,
    request_timeout: float = 60.0,
    default_headers: dict[str, str] | None = None,
    categories: list[str] | None = None,
) -> ComplianceReport:
    """Run compliance harness v2 against a remote ASAP agent over HTTP(S).

    Args:
        base_url: Agent root URL (e.g. ``http://127.0.0.1:8000``). Trailing slashes are stripped.
        request_timeout: Per-request timeout in seconds.
        default_headers: Optional headers merged into every request (e.g. ``ASAP-Version``).
        categories: Categories to check. Defaults to all.

    Returns:
        ComplianceReport with all check results and score.

    Raises:
        httpx.ConnectError: If the TCP connection cannot be established (preflight ``GET /``).
        httpx.TimeoutException: If the preflight request times out.

    Note:
        Redirects are not followed. Auto-registration validates the harness
        URL once, then calls this runner; following ``Location`` would let a
        public agent bounce the registry-bot (or ``asap compliance-check``)
        onto link-local/IMDS targets after that check. Same policy as
        ``fetch_manifest_at_url``.
    """
    normalized = base_url.rstrip("/")
    timeout_cfg = Timeout(request_timeout)
    async with AsyncClient(
        base_url=normalized,
        timeout=timeout_cfg,
        headers=default_headers,
        follow_redirects=False,
    ) as client:
        # Fail fast on unreachable hosts so callers (e.g. CLI) can treat as transport error,
        # instead of a synthetic all-failed report from per-check exception handlers.
        try:
            await client.get("/")
        except httpx.ConnectError:
            raise
        except httpx.TimeoutException:
            raise
        return await run_compliance_harness_with_client(client, categories=categories)
