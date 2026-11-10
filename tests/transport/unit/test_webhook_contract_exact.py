"""Lock webhook HMAC wire format and the 4xx/5xx retry boundary."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    assert verify_signature(body, secret, f"sha256={hex_digest}aa") is False


async def _deliver(responses: list[WebhookResult]) -> tuple[WebhookResult, int]:
    """Run one retry manager and return the result plus the delivery count."""
    delivery = _delivery(responses)
    manager = WebhookRetryManager(delivery, policy=_FAST_POLICY)
    result = await manager.deliver_with_retry(_URL, {"event": "task.completed"})
    return result, delivery.deliver.call_count


@pytest.mark.parametrize("status_code", [400, 404, 499])
async def test_webhook_client_status_is_not_retried(status_code: int) -> None:
    """400 through 499 are one attempt. A 401-500 range would still retry 400."""
    result, calls = await _deliver([_result(status_code, success=False)])
    assert result.status_code == status_code
    assert result.success is False
    assert calls == 1


@pytest.mark.parametrize("status_code", [399, 500, 502])
async def test_webhook_status_outside_4xx_is_retried(status_code: int) -> None:
    """399 and 5xx are retried. An equality check on 499 would not retry 399."""
    result, calls = await _deliver(
        [_result(status_code, success=False), _result(200, success=True)]
    )
    assert result.success is True
    assert result.status_code == 200
    assert calls == 2
