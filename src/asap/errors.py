"""ASAP Protocol Error Taxonomy.

This module defines the error hierarchy for the ASAP protocol,
providing structured error handling with taxonomy codes, JSON-RPC
numeric codes in the reserved application range (-32000 to -32059),
and recovery hints (PRD §4.7).
"""

from __future__ import annotations

from typing import Any

# --- JSON-RPC application error range (PRD §4.7) ---------------------------------

JSON_RPC_ASAP_MIN = -32059
JSON_RPC_ASAP_MAX = -32000

# Protocol (-32000..-32009)
RPC_INVALID_STATE = -32000
RPC_MALFORMED_ENVELOPE = -32001
RPC_INVALID_TIMESTAMP = -32002
RPC_INVALID_NONCE = -32003

# Routing (-32010..-32019)
RPC_TASK_NOT_FOUND = -32010
RPC_CIRCUIT_OPEN = -32011
RPC_HANDLER_NOT_FOUND = -32012

# Capability (-32020..-32029)
RPC_UNSUPPORTED_AUTH_SCHEME = -32020

# Execution / transport client (-32030..-32039)
RPC_TASK_ALREADY_COMPLETED = -32030
RPC_THREAD_POOL_EXHAUSTED = -32031
RPC_CONNECTION_ERROR = -32032
RPC_TIMEOUT = -32033
RPC_REMOTE_GENERIC = -32034

# Resource (-32040..-32049)
RPC_RESOURCE_EXHAUSTED = -32040

# Security (-32050..-32059)
RPC_WEBHOOK_URL_REJECTED = -32050
RPC_AGENT_REVOKED = -32051
RPC_SIGNATURE_VERIFICATION = -32052


def is_asap_json_rpc_code(code: int) -> bool:
    """Return True if *code* lies in the ASAP reserved JSON-RPC band."""
    return JSON_RPC_ASAP_MIN <= code <= JSON_RPC_ASAP_MAX


class ASAPError(Exception):
    """Base exception for all ASAP protocol errors.

    Attributes:
        code: Taxonomy URI (e.g. ``asap:protocol/...``) per ADR-012.
        rpc_code: Integer JSON-RPC application error code (-32000..-32059).
        message: Human-readable error message.
        details: Optional structured context.
        retry_after_ms: Suggested client wait before retry (recoverable hints).
        alternative_agents: Optional list of agent URNs to try instead.
        fallback_action: Optional machine-readable suggested next step.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        rpc_code: int = RPC_REMOTE_GENERIC,
        retry_after_ms: int | None = None,
        alternative_agents: list[str] | None = None,
        fallback_action: str | None = None,
    ) -> None:
        if not is_asap_json_rpc_code(rpc_code):
            raise ValueError(
                f"rpc_code must be in [{JSON_RPC_ASAP_MIN}, {JSON_RPC_ASAP_MAX}], got {rpc_code}"
            )
        super().__init__(message)
        self.code = code
        self.rpc_code = rpc_code
        self.message = message
        self.details = details or {}
        self.retry_after_ms = retry_after_ms
        self.alternative_agents = alternative_agents
        self.fallback_action = fallback_action

    def to_dict(self) -> dict[str, Any]:
        """Serialize taxonomy, JSON-RPC code, message, details, and recovery hints."""
        result: dict[str, Any] = {
            "code": self.code,
            "rpc_code": self.rpc_code,
            "message": self.message,
            "details": self.details,
        }
        if self.retry_after_ms is not None:
            result["retry_after_ms"] = self.retry_after_ms
        if self.alternative_agents is not None:
            result["alternative_agents"] = self.alternative_agents
        if self.fallback_action is not None:
            result["fallback_action"] = self.fallback_action
        return result


class RecoverableError(ASAPError):
    """Errors that may succeed after retry or alternate routing."""


class FatalError(ASAPError):
    """Errors that should not be retried without fixing input or policy."""


class InvalidTransitionError(FatalError):
    """Raised when attempting an invalid task state transition."""

    def __init__(
        self, from_state: str, to_state: str, details: dict[str, Any] | None = None
    ) -> None:
        message = f"Invalid transition from '{from_state}' to '{to_state}'"
        super().__init__(
            "asap:protocol/invalid_state",
            message,
            {"from_state": from_state, "to_state": to_state, **(details or {})},
            rpc_code=RPC_INVALID_STATE,
        )
        self.from_state = from_state
        self.to_state = to_state


class MalformedEnvelopeError(FatalError):
    """Raised when receiving a malformed or invalid envelope."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None) -> None:
        message = f"Malformed envelope: {reason}"
        super().__init__(
            "asap:protocol/malformed_envelope",
            message,
            details or {},
            rpc_code=RPC_MALFORMED_ENVELOPE,
        )
        self.reason = reason


class TaskNotFoundError(FatalError):
    """Raised when a requested task cannot be found."""

    def __init__(self, task_id: str, details: dict[str, Any] | None = None) -> None:
        message = f"Task not found: {task_id}"
        super().__init__(
            "asap:task/not_found",
            message,
            {"task_id": task_id, **(details or {})},
            rpc_code=RPC_TASK_NOT_FOUND,
        )
        self.task_id = task_id


class TaskAlreadyCompletedError(FatalError):
    """Raised when attempting to modify a task that is already completed."""

    def __init__(
        self, task_id: str, current_status: str, details: dict[str, Any] | None = None
    ) -> None:
        message = f"Task already completed: {task_id} (status: {current_status})"
        super().__init__(
            "asap:task/already_completed",
            message,
            {"task_id": task_id, "current_status": current_status, **(details or {})},
            rpc_code=RPC_TASK_ALREADY_COMPLETED,
        )
        self.task_id = task_id
        self.current_status = current_status


class ThreadPoolExhaustedError(RecoverableError):
    """Raised when the thread pool is exhausted and cannot accept new tasks."""

    def __init__(
        self,
        max_threads: int,
        active_threads: int,
        details: dict[str, Any] | None = None,
        *,
        retry_after_ms: int | None = 1_000,
    ) -> None:
        message = (
            f"Thread pool exhausted: {active_threads}/{max_threads} threads in use. "
            "Service temporarily unavailable."
        )
        super().__init__(
            "asap:transport/thread_pool_exhausted",
            message,
            {
                "max_threads": max_threads,
                "active_threads": active_threads,
                **(details or {}),
            },
            rpc_code=RPC_THREAD_POOL_EXHAUSTED,
            retry_after_ms=retry_after_ms,
            fallback_action="retry_later",
        )
        self.max_threads = max_threads
        self.active_threads = active_threads


class InvalidTimestampError(RecoverableError):
    """Raised when an envelope timestamp is invalid (skew / replay window)."""

    def __init__(
        self,
        timestamp: str,
        message: str,
        age_seconds: float | None = None,
        future_offset_seconds: float | None = None,
        details: dict[str, Any] | None = None,
        *,
        retry_after_ms: int | None = 5_000,
    ) -> None:
        details_dict: dict[str, Any] = {"timestamp": timestamp}
        if age_seconds is not None:
            details_dict["age_seconds"] = age_seconds
        if future_offset_seconds is not None:
            details_dict["future_offset_seconds"] = future_offset_seconds
        if details:
            details_dict.update(details)

        super().__init__(
            "asap:protocol/invalid_timestamp",
            message,
            details_dict,
            rpc_code=RPC_INVALID_TIMESTAMP,
            retry_after_ms=retry_after_ms,
            fallback_action="resend_with_fresh_timestamp",
        )
        self.timestamp = timestamp
        self.age_seconds = age_seconds
        self.future_offset_seconds = future_offset_seconds


class InvalidNonceError(FatalError):
    """Raised when an envelope nonce is invalid (duplicate or malformed)."""

    def __init__(
        self,
        nonce: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            "asap:protocol/invalid_nonce",
            message,
            {
                "nonce": nonce,
                **(details or {}),
            },
            rpc_code=RPC_INVALID_NONCE,
        )
        self.nonce = nonce


class CircuitOpenError(RecoverableError):
    """Raised when circuit breaker is open and request is rejected."""

    def __init__(
        self,
        base_url: str,
        consecutive_failures: int,
        details: dict[str, Any] | None = None,
        *,
        retry_after_ms: int | None = 5_000,
    ) -> None:
        message = (
            f"Circuit breaker is OPEN for {base_url}. "
            f"Too many consecutive failures ({consecutive_failures}). "
            "Service temporarily unavailable."
        )
        super().__init__(
            "asap:transport/circuit_open",
            message,
            {
                "base_url": base_url,
                "consecutive_failures": consecutive_failures,
                **(details or {}),
            },
            rpc_code=RPC_CIRCUIT_OPEN,
            retry_after_ms=retry_after_ms,
            fallback_action="retry_later_or_failover",
        )
        self.base_url = base_url
        self.consecutive_failures = consecutive_failures


class WebhookURLValidationError(FatalError):
    """Raised when a webhook callback URL fails SSRF validation."""

    def __init__(
        self,
        url: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = f"Webhook URL rejected: {reason}"
        super().__init__(
            "asap:transport/webhook_url_rejected",
            message,
            {
                "url": url,
                "reason": reason,
                **(details or {}),
            },
            rpc_code=RPC_WEBHOOK_URL_REJECTED,
        )
        self.url = url
        self.reason = reason


class AgentRevokedException(FatalError):
    """Raised when agent URN is in revoked_agents.json."""

    def __init__(self, urn: str, details: dict[str, Any] | None = None) -> None:
        message = f"Agent revoked: {urn}"
        super().__init__(
            "asap:agent/revoked",
            message,
            {"urn": urn, **(details or {})},
            rpc_code=RPC_AGENT_REVOKED,
        )
        self.urn = urn


class SignatureVerificationError(FatalError):
    """Tampering, wrong algorithm, or invalid/corrupted signature; see message for cause."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            "asap:error/signature-verification",
            message,
            details or {},
            rpc_code=RPC_SIGNATURE_VERIFICATION,
        )


class UnsupportedAuthSchemeError(FatalError):
    """Raised when an unsupported authentication scheme is specified."""

    def __init__(
        self,
        scheme: str,
        supported_schemes: set[str] | frozenset[str],
        details: dict[str, Any] | None = None,
    ) -> None:
        supported_list = sorted(supported_schemes)
        message = (
            f"Unsupported authentication scheme '{scheme}'. "
            f"Supported schemes: {', '.join(supported_list)}"
        )
        super().__init__(
            "asap:auth/unsupported_scheme",
            message,
            {
                "scheme": scheme,
                "supported_schemes": list(supported_list),
                **(details or {}),
            },
            rpc_code=RPC_UNSUPPORTED_AUTH_SCHEME,
        )
        self.scheme = scheme
        self.supported_schemes = supported_schemes


class ASAPConnectionError(RecoverableError):
    """Raised when connection to remote agent fails (HTTP layer)."""

    def __init__(
        self,
        message: str,
        cause: Exception | None = None,
        url: str | None = None,
        *,
        rpc_code: int = RPC_CONNECTION_ERROR,
        retry_after_ms: int | None = None,
        alternative_agents: list[str] | None = None,
    ) -> None:
        if url and "Verify" not in message and "troubleshooting" not in message.lower():
            enhanced_message = (
                f"{message}\n"
                f"Troubleshooting: Connection failed to {url}. "
                "Verify the agent is running and accessible. "
                "Check the URL format, network connectivity, and firewall settings."
            )
        else:
            enhanced_message = message

        details: dict[str, Any] = {}
        if url is not None:
            details["url"] = url
        if cause is not None:
            details["cause"] = str(cause)
        super().__init__(
            "asap:transport/connection_error",
            enhanced_message,
            details,
            rpc_code=rpc_code,
            retry_after_ms=retry_after_ms,
            alternative_agents=alternative_agents,
            fallback_action="retry_or_check_connectivity",
        )
        self.cause = cause
        self.url = url


class ASAPTimeoutError(RecoverableError):
    """Raised when request to remote agent times out."""

    def __init__(
        self,
        message: str,
        timeout: float | None = None,
        *,
        retry_after_ms: int | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if timeout is not None:
            details["timeout_seconds"] = timeout
        super().__init__(
            "asap:transport/timeout",
            message,
            details,
            rpc_code=RPC_TIMEOUT,
            retry_after_ms=retry_after_ms,
            fallback_action="retry_with_backoff",
        )
        self.timeout = timeout


def _pop_remote_meta(
    raw: dict[str, Any],
) -> tuple[str, dict[str, Any], int | None, list[str] | None, str | None]:
    """Extract taxonomy and recovery hints; return cleaned details dict."""
    d = dict(raw)
    taxonomy = str(d.pop("asap_taxonomy_code", "asap:rpc/remote_error"))
    d.pop("recoverable", None)
    retry_raw = d.pop("retry_after_ms", None)
    agents_raw = d.pop("alternative_agents", None)
    fallback_raw = d.pop("fallback_action", None)
    d.pop("rpc_code", None)
    taxonomy_alt = d.pop("code", None)
    if isinstance(taxonomy_alt, str) and taxonomy_alt.startswith("asap:"):
        taxonomy = taxonomy_alt
    retry_after_ms = int(retry_raw) if retry_raw is not None else None
    alternative_agents = list(agents_raw) if isinstance(agents_raw, list) else None
    fallback_action = str(fallback_raw) if fallback_raw is not None else None
    return taxonomy, d, retry_after_ms, alternative_agents, fallback_action


class RemoteRPCError(ASAPError):
    """JSON-RPC error returned by a remote ASAP peer (client-side).

    One class for fatal and recoverable remote failures (v2.5.1 S3 collapse;
    v2.6.0 removed the fatal and recoverable subclasses). Recoverability is
    a runtime property (``is_recoverable``), not a distinct type, so callers
    branch on the property instead of ``isinstance``.

    Attributes:
        json_rpc_code: The wire JSON-RPC code as received from the peer
            (preserved verbatim, even outside the ASAP reserved band).
        is_recoverable: Whether the peer hinted the failure is retryable
            (``data.recoverable`` was ``True``). Drives the client retry
            decision without a second exception class.

    Example:
        >>> err = RemoteRPCError.from_jsonrpc(-32033, "timeout", {"recoverable": True})
        >>> err.is_recoverable
        True
        >>> err.json_rpc_code
        -32033
    """

    json_rpc_code: int

    def __init__(
        self,
        wire_jsonrpc_code: int,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        is_recoverable: bool = False,
        taxonomy_code: str = "asap:rpc/remote_error",
        retry_after_ms: int | None = None,
        alternative_agents: list[str] | None = None,
        fallback_action: str | None = None,
    ) -> None:
        asap_slot = (
            wire_jsonrpc_code if is_asap_json_rpc_code(wire_jsonrpc_code) else RPC_REMOTE_GENERIC
        )
        super().__init__(
            taxonomy_code,
            message,
            details or {},
            rpc_code=asap_slot,
            retry_after_ms=retry_after_ms,
            alternative_agents=alternative_agents,
            fallback_action=fallback_action,
        )
        self.json_rpc_code = wire_jsonrpc_code
        self.is_recoverable = is_recoverable

    def __str__(self) -> str:
        return f"Remote error {self.json_rpc_code}: {self.message}"

    @property
    def data(self) -> dict[str, Any]:
        return self.details

    @classmethod
    def from_jsonrpc(
        cls, rpc_code: int, message: str, data: dict[str, Any] | None
    ) -> RemoteRPCError:
        raw = dict(data or {})
        taxonomy, details, retry_after_ms, alternative_agents, fallback_action = _pop_remote_meta(
            raw
        )
        return cls(
            rpc_code,
            message,
            details,
            is_recoverable=raw.get("recoverable") is True,
            taxonomy_code=taxonomy,
            retry_after_ms=retry_after_ms,
            alternative_agents=alternative_agents,
            fallback_action=fallback_action,
        )


ASAPRemoteError = RemoteRPCError


def remote_rpc_error_from_json(
    rpc_code: int,
    message: str,
    data: dict[str, Any] | None,
) -> RemoteRPCError:
    """Construct a typed remote error from JSON-RPC *error* fields.

    The returned ``RemoteRPCError`` carries ``is_recoverable`` derived from the
    peer's ``data.recoverable`` hint, so callers no longer need a second
    exception class to distinguish retryable from fatal remote failures.
    """
    return RemoteRPCError.from_jsonrpc(rpc_code, message, data)


def jsonrpc_error_data_for_asap_exception(exc: ASAPError) -> dict[str, Any]:
    """Shape *data* for JSON-RPC error payloads (server to client)."""
    payload = exc.to_dict()
    payload["recoverable"] = isinstance(exc, RecoverableError)
    payload["asap_taxonomy_code"] = exc.code
    return payload
