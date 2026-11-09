"""Lock webhook HMAC wire format and the 4xx/5xx retry boundary."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

from asap.transport.webhook import (
    RetryPolicy,
    WebhookDelivery,
    WebhookResult,
    WebhookRetryManager,
    compute_signature,
    verify_signature,
)

_URL = "https://receiver.example.com/hook"
_FAST_POLICY = RetryPolicy(max_retries=3, base_delay=0.0, max_delay=0.0, rate_per_second=1000.0)


def _result(status_code: int, *, success: bool) -> WebhookResult:
    return WebhookResult(
        url=_URL,
        status_code=status_code,
        success=success,
        elapsed_ms=1.0,
        error=None if success else f"status {status_code}",
    )


def _delivery(responses: list[WebhookResult]) -> WebhookDelivery:
    delivery = cast(WebhookDelivery, MagicMock(spec=WebhookDelivery))
    delivery.deliver = AsyncMock(side_effect=responses)
    return delivery


def test_verify_signature_requires_the_sha256_prefix() -> None:
    """Receivers accept only ``sha256=`` plus the lowercase hex digest."""
    body = b'{"event":"task.completed"}'
    secret = b"webhook-secret"
    signed = compute_signature(body, secret)
    hex_digest = signed.removeprefix("sha256=")

    assert verify_signature(body, secret, signed) is True
    assert verify_signature(body, secret, hex_digest) is False
    assert verify_signature(body, secret, f"SHA256={hex_digest}") is False
    assert verify_signature(body, secret, f"sha256={hex_digest.upper()}") is False


async def test_webhook_retry_stops_at_499_and_continues_at_500() -> None:
    """499 is the last non-retryable client status. 500 is a server retry."""
    client_delivery = _delivery([_result(499, success=False)])
    client_manager = WebhookRetryManager(client_delivery, policy=_FAST_POLICY)
    client_result = await client_manager.deliver_with_retry(_URL, {"event": "task.completed"})
    assert client_result.status_code == 499
    assert client_result.success is False
    assert client_delivery.deliver.call_count == 1

    server_delivery = _delivery([_result(500, success=False), _result(200, success=True)])
    server_manager = WebhookRetryManager(server_delivery, policy=_FAST_POLICY)
    server_result = await server_manager.deliver_with_retry(_URL, {"event": "task.completed"})
    assert server_result.success is True
    assert server_result.status_code == 200
    assert server_delivery.deliver.call_count == 2
