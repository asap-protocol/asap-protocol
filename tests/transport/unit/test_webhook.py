"""Unit tests for webhook delivery, SSRF validation, HMAC signing, and retry logic."""

from __future__ import annotations

import json
import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from asap.errors import WebhookURLValidationError
from asap.transport.webhook import (
    MAX_DEAD_LETTERS,
    X_ASAP_SIGNATURE_HEADER,
    DeadLetterEntry,
    RetryPolicy,
    WebhookDelivery,
    WebhookResult,
    WebhookRetryManager,
    compute_signature,
    validate_callback_url,
    verify_signature,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _public_addrinfo() -> list[tuple[int, int, int, str, tuple[str, int]]]:
    """Return a fake getaddrinfo result pointing to a public IP."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def _private_addrinfo() -> list[tuple[int, int, int, str, tuple[str, int]]]:
    """Return a fake getaddrinfo result pointing to a private IP (DNS rebinding)."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 0))]


def _patch_async_getaddrinfo(
    return_value: list | None = None, side_effect: Exception | None = None
):
    """Patch asyncio event loop getaddrinfo with AsyncMock."""
    mock = AsyncMock()
    if side_effect is not None:
        mock.side_effect = side_effect
    else:
        mock.return_value = return_value or _public_addrinfo()
    return patch("asyncio.get_running_loop", return_value=MagicMock(getaddrinfo=mock))


# ---------------------------------------------------------------------------
# TestURLValidation — SSRF prevention
# ---------------------------------------------------------------------------


class TestURLValidation:
    async def test_https_allowed_when_required(self) -> None:
        with _patch_async_getaddrinfo(_public_addrinfo()):
            await validate_callback_url("https://example.com/webhook", require_https=True)

    async def test_http_allowed_when_relaxed(self) -> None:
        with _patch_async_getaddrinfo(_public_addrinfo()):
            await validate_callback_url("http://example.com/webhook", require_https=False)

    async def test_http_blocked_when_required(self) -> None:
        with pytest.raises(WebhookURLValidationError, match="Scheme 'http' is not allowed"):
            await validate_callback_url("http://example.com/webhook", require_https=True)

    async def test_ftp_scheme_blocked(self) -> None:
        with pytest.raises(WebhookURLValidationError, match="Scheme 'ftp' is not allowed"):
            await validate_callback_url("ftp://example.com/file", require_https=False)

    # -- Missing hostname --

    async def test_missing_hostname_blocked(self) -> None:
        with pytest.raises(WebhookURLValidationError, match="must include a hostname"):
            await validate_callback_url("https://", require_https=True)

    # -- Private IP literal checks --

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "10.0.0.1",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.0.1",
            "192.168.255.255",
        ],
        ids=[
            "loopback-127",
            "private-10-start",
            "private-10-end",
            "private-172-start",
            "private-172-end",
            "private-192-start",
            "private-192-end",
        ],
    )
    async def test_private_ipv4_blocked(self, ip: str) -> None:
        with pytest.raises(WebhookURLValidationError, match="blocked address range"):
            await validate_callback_url(f"https://{ip}/hook")

    async def test_link_local_ipv4_blocked(self) -> None:
        with pytest.raises(WebhookURLValidationError, match="blocked address range"):
            await validate_callback_url("https://169.254.1.1/hook")

    async def test_ipv6_loopback_blocked(self) -> None:
        with pytest.raises(WebhookURLValidationError, match="blocked address range"):
            await validate_callback_url("https://[::1]/hook")

    @pytest.mark.parametrize(
        "ip",
        [
            "::ffff:192.168.1.1",
            "::ffff:10.0.0.1",
            "::ffff:169.254.169.254",
            "240.0.0.1",
        ],
    )
    async def test_mapped_and_reserved_literals_blocked(self, ip: str) -> None:
        """IPv4-mapped and Class E literals are blocked before DNS."""
        url = f"https://[{ip}]/hook" if ":" in ip else f"https://{ip}/hook"
        with pytest.raises(WebhookURLValidationError, match="blocked address range"):
            await validate_callback_url(url)

    # -- DNS rebinding --

    async def test_dns_rebinding_blocked(self) -> None:
        with (
            _patch_async_getaddrinfo(_private_addrinfo()),
            pytest.raises(WebhookURLValidationError, match="resolved to blocked IP"),
        ):
            await validate_callback_url("https://evil.example.com/hook")

    async def test_dns_rebinding_to_ipv4_mapped_private_blocked(self) -> None:
        """An AAAA answer of ::ffff:192.168.1.1 is a private address, not a public one."""
        mapped = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:192.168.1.1", 0, 0, 0)),
        ]
        with (
            _patch_async_getaddrinfo(mapped),
            pytest.raises(WebhookURLValidationError, match="::ffff:192.168.1.1"),
        ):
            await validate_callback_url("https://rebind.example.com/hook")

    async def test_dns_resolution_failure_raises(self) -> None:
        with (
            _patch_async_getaddrinfo(side_effect=socket.gaierror("DNS failed")),
            pytest.raises(WebhookURLValidationError, match="DNS resolution failed"),
        ):
            await validate_callback_url("https://nonexistent.invalid/hook")

    # -- Public IP passes --

    async def test_public_ip_allowed(self) -> None:
        with _patch_async_getaddrinfo(_public_addrinfo()):
            await validate_callback_url("https://93.184.216.34/hook")

    # -- Error details --

    async def test_error_includes_url_and_reason(self) -> None:
        with pytest.raises(WebhookURLValidationError) as exc_info:
            await validate_callback_url("http://example.com/hook", require_https=True)

        err = exc_info.value
        assert err.url == "http://example.com/hook"
        assert "http" in err.reason.lower()
        assert err.code == "asap:transport/webhook_url_rejected"


# ---------------------------------------------------------------------------
# TestSignature — HMAC-SHA256
# ---------------------------------------------------------------------------


class TestSignature:
    _SECRET = b"test-webhook-secret"
    _BODY = b'{"event":"task.completed","task_id":"abc-123"}'

    def test_compute_signature_has_sha256_prefix(self) -> None:
        sig = compute_signature(self._BODY, self._SECRET)
        assert sig.startswith("sha256=")

    def test_compute_signature_hex_length(self) -> None:
        sig = compute_signature(self._BODY, self._SECRET)
        hex_part = sig.removeprefix("sha256=")
        assert len(hex_part) == 64

    def test_compute_signature_deterministic(self) -> None:
        sig1 = compute_signature(self._BODY, self._SECRET)
        sig2 = compute_signature(self._BODY, self._SECRET)
        assert sig1 == sig2

    def test_verify_signature_correct_secret(self) -> None:
        sig = compute_signature(self._BODY, self._SECRET)
        assert verify_signature(self._BODY, self._SECRET, sig) is True

    def test_verify_signature_wrong_secret_rejected(self) -> None:
        sig = compute_signature(self._BODY, self._SECRET)
        assert verify_signature(self._BODY, b"wrong-secret", sig) is False

    def test_verify_signature_tampered_body_rejected(self) -> None:
        sig = compute_signature(self._BODY, self._SECRET)
        tampered = self._BODY + b"x"
        assert verify_signature(tampered, self._SECRET, sig) is False

    def test_verify_signature_malformed_rejected(self) -> None:
        assert verify_signature(self._BODY, self._SECRET, "not-a-real-sig") is False

    def test_different_bodies_produce_different_signatures(self) -> None:
        sig_a = compute_signature(b"body-a", self._SECRET)
        sig_b = compute_signature(b"body-b", self._SECRET)
        assert sig_a != sig_b


# ---------------------------------------------------------------------------
# TestWebhookDelivery — async deliver()
# ---------------------------------------------------------------------------


class TestWebhookDelivery:
    _SECRET = b"deliver-test-secret"
    _PAYLOAD: dict[str, Any] = {"event": "task.completed", "task_id": "t-1"}
    _URL = "https://receiver.example.com/hook"

    @pytest.fixture()
    def _mock_dns(self):
        """Fixture that patches async DNS resolution to return a public IP."""
        with _patch_async_getaddrinfo(_public_addrinfo()):
            yield

    # -- Success --

    @pytest.mark.usefixtures("_mock_dns")
    async def test_deliver_success_200(self) -> None:
        delivery = WebhookDelivery(secret=self._SECRET, require_https=True)

        with patch("asap.transport.webhook.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(200)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await delivery.deliver(self._URL, self._PAYLOAD)

        assert isinstance(result, WebhookResult)
        assert result.success is True
        assert result.status_code == 200
        assert result.url == self._URL
        assert result.error is None
        assert result.elapsed_ms >= 0

    # -- HMAC header present --

    @pytest.mark.usefixtures("_mock_dns")
    async def test_deliver_includes_hmac_header(self) -> None:
        delivery = WebhookDelivery(secret=self._SECRET, require_https=True)

        with patch("asap.transport.webhook.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(200)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            await delivery.deliver(self._URL, self._PAYLOAD)

            # Inspect headers passed to post()
            call_kwargs = mock_client.post.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert X_ASAP_SIGNATURE_HEADER in headers
            assert headers[X_ASAP_SIGNATURE_HEADER].startswith("sha256=")

    # -- No HMAC when secret is None --

    @pytest.mark.usefixtures("_mock_dns")
    async def test_deliver_no_hmac_without_secret(self) -> None:
        delivery = WebhookDelivery(secret=None, require_https=True)

        with patch("asap.transport.webhook.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(200)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            await delivery.deliver(self._URL, self._PAYLOAD)

            call_kwargs = mock_client.post.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert X_ASAP_SIGNATURE_HEADER not in headers

    # -- Extra headers --

    @pytest.mark.usefixtures("_mock_dns")
    async def test_deliver_extra_headers_included(self) -> None:
        delivery = WebhookDelivery(secret=None, require_https=True)
        extra = {"X-Custom": "value"}

        with patch("asap.transport.webhook.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(200)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            await delivery.deliver(self._URL, self._PAYLOAD, extra_headers=extra)

            call_kwargs = mock_client.post.call_args
            headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
            assert headers["X-Custom"] == "value"

    # -- 4xx / 5xx --

    @pytest.mark.parametrize("status", [400, 403, 404, 500, 502, 503])
    @pytest.mark.usefixtures("_mock_dns")
    async def test_deliver_non_2xx_returns_failure(self, status: int) -> None:
        delivery = WebhookDelivery(secret=None, require_https=True)

        with patch("asap.transport.webhook.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(status)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await delivery.deliver(self._URL, self._PAYLOAD)

        assert result.success is False
        assert result.status_code == status

    # -- Timeout --

    @pytest.mark.usefixtures("_mock_dns")
    async def test_deliver_timeout_returns_error_result(self) -> None:
        delivery = WebhookDelivery(secret=None, require_https=True, timeout_seconds=1.0)

        with patch("asap.transport.webhook.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ReadTimeout("timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await delivery.deliver(self._URL, self._PAYLOAD)

        assert result.success is False
        assert result.status_code == 0
        assert result.error is not None
        assert "Timeout" in result.error

    # -- Network error --

    @pytest.mark.usefixtures("_mock_dns")
    async def test_deliver_network_error_returns_error_result(self) -> None:
        delivery = WebhookDelivery(secret=None, require_https=True)

        with patch("asap.transport.webhook.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await delivery.deliver(self._URL, self._PAYLOAD)

        assert result.success is False
        assert result.status_code == 0
        assert "connection refused" in (result.error or "")

    # -- SSRF checked before HTTP call --

    async def test_deliver_ssrf_blocks_before_http(self) -> None:
        delivery = WebhookDelivery(secret=None, require_https=False)

        with pytest.raises(WebhookURLValidationError, match="blocked address range"):
            await delivery.deliver("http://127.0.0.1/hook", {"x": 1})

    # -- Payload serialization is deterministic --

    @pytest.mark.usefixtures("_mock_dns")
    async def test_deliver_payload_sorted_keys(self) -> None:
        delivery = WebhookDelivery(secret=self._SECRET, require_https=True)
        payload = {"z_last": 1, "a_first": 2}

        with patch("asap.transport.webhook.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = httpx.Response(200)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            await delivery.deliver(self._URL, payload)

            call_kwargs = mock_client.post.call_args
            body = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content")
            parsed = json.loads(body)
            keys = list(parsed.keys())
            assert keys == sorted(keys), "JSON keys should be sorted for deterministic HMAC"

    # -- WebhookDelivery.validate_url delegates --

    async def test_validate_url_delegates_to_module_function(self) -> None:
        delivery_strict = WebhookDelivery(require_https=True)
        delivery_relaxed = WebhookDelivery(require_https=False)

        # Strict should block HTTP
        with pytest.raises(WebhookURLValidationError):
            await delivery_strict.validate_url("http://example.com/hook")

        # Relaxed should allow HTTP (DNS is resolved — mock it)
        with _patch_async_getaddrinfo(_public_addrinfo()):
            await delivery_relaxed.validate_url("http://example.com/hook")


# ---------------------------------------------------------------------------
# TestRetryPolicy — backoff computation
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_default_values(self) -> None:
        policy = RetryPolicy()
        assert policy.max_retries == 5
        assert policy.base_delay == 1.0
        assert policy.max_delay == 16.0
        assert policy.rate_per_second == 10.0

    def test_backoff_doubles_each_attempt(self) -> None:
        policy = RetryPolicy()
        delays = [policy.backoff_delay(i) for i in range(5)]
        assert delays == [1.0, 2.0, 4.0, 8.0, 16.0]

    def test_backoff_capped_at_max_delay(self) -> None:
        policy = RetryPolicy(max_delay=4.0)
        assert policy.backoff_delay(0) == 1.0
        assert policy.backoff_delay(1) == 2.0
        assert policy.backoff_delay(2) == 4.0
        assert policy.backoff_delay(3) == 4.0  # capped
        assert policy.backoff_delay(10) == 4.0  # still capped

    def test_custom_base_delay(self) -> None:
        policy = RetryPolicy(base_delay=0.5, max_delay=8.0)
        assert policy.backoff_delay(0) == 0.5
        assert policy.backoff_delay(1) == 1.0
        assert policy.backoff_delay(2) == 2.0


# ---------------------------------------------------------------------------
# Helpers for retry manager tests
# ---------------------------------------------------------------------------


def _make_mock_delivery(responses: list[WebhookResult]) -> WebhookDelivery:
    delivery = MagicMock(spec=WebhookDelivery)
    delivery.deliver = AsyncMock(side_effect=responses)
    delivery.validate_url = MagicMock()  # no-op
    return delivery


def _ok_result(url: str = "https://example.com/hook") -> WebhookResult:
    return WebhookResult(url=url, status_code=200, success=True, elapsed_ms=10.0)


def _server_error(url: str = "https://example.com/hook", code: int = 500) -> WebhookResult:
    return WebhookResult(
        url=url,
        status_code=code,
        success=False,
        elapsed_ms=10.0,
        error="Internal Server Error",
    )


def _client_error(url: str = "https://example.com/hook", code: int = 400) -> WebhookResult:
    return WebhookResult(
        url=url,
        status_code=code,
        success=False,
        elapsed_ms=10.0,
        error="Bad Request",
    )


def _network_error(url: str = "https://example.com/hook") -> WebhookResult:
    return WebhookResult(
        url=url,
        status_code=0,
        success=False,
        elapsed_ms=10.0,
        error="connection refused",
    )


# ---------------------------------------------------------------------------
# TestWebhookRetryManager — retry, backoff, DLQ, rate limiting
# ---------------------------------------------------------------------------


class TestWebhookRetryManager:
    _URL = "https://receiver.example.com/hook"
    _PAYLOAD: dict[str, Any] = {"event": "task.completed"}

    # Use a very fast policy so tests don't actually sleep.
    _FAST_POLICY = RetryPolicy(max_retries=3, base_delay=0.0, max_delay=0.0, rate_per_second=1000.0)

    # -- Immediate success --

    async def test_success_on_first_attempt(self) -> None:
        delivery = _make_mock_delivery([_ok_result(self._URL)])
        manager = WebhookRetryManager(delivery, policy=self._FAST_POLICY)

        result = await manager.deliver_with_retry(self._URL, self._PAYLOAD)

        assert result.success is True
        assert delivery.deliver.call_count == 1
        assert len(manager.dead_letters) == 0

    # -- Retry on 5xx then succeed --

    async def test_retry_on_5xx_then_success(self) -> None:
        delivery = _make_mock_delivery(
            [
                _server_error(self._URL, 503),
                _ok_result(self._URL),
            ]
        )
        manager = WebhookRetryManager(delivery, policy=self._FAST_POLICY)

        result = await manager.deliver_with_retry(self._URL, self._PAYLOAD)

        assert result.success is True
        assert delivery.deliver.call_count == 2

    # -- Retry on network error then succeed --

    async def test_retry_on_network_error_then_success(self) -> None:
        delivery = _make_mock_delivery(
            [
                _network_error(self._URL),
                _ok_result(self._URL),
            ]
        )
        manager = WebhookRetryManager(delivery, policy=self._FAST_POLICY)

        result = await manager.deliver_with_retry(self._URL, self._PAYLOAD)

        assert result.success is True
        assert delivery.deliver.call_count == 2

    # -- No retry on 4xx --

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    async def test_no_retry_on_4xx(self, status: int) -> None:
        delivery = _make_mock_delivery([_client_error(self._URL, status)])
        manager = WebhookRetryManager(delivery, policy=self._FAST_POLICY)

        result = await manager.deliver_with_retry(self._URL, self._PAYLOAD)

        assert result.success is False
        assert result.status_code == status
        assert delivery.deliver.call_count == 1
        assert len(manager.dead_letters) == 0

    # -- DLQ after max retries --

    async def test_dead_letter_after_max_retries(self) -> None:
        # 1 initial + 3 retries = 4 total attempts
        responses = [_server_error(self._URL, 500)] * 4
        delivery = _make_mock_delivery(responses)
        manager = WebhookRetryManager(delivery, policy=self._FAST_POLICY)

        result = await manager.deliver_with_retry(self._URL, self._PAYLOAD)

        assert result.success is False
        assert delivery.deliver.call_count == 4  # 1 initial + 3 retries
        assert len(manager.dead_letters) == 1

        entry = manager.dead_letters[0]
        assert entry.url == self._URL
        assert entry.payload == self._PAYLOAD
        assert entry.attempts == 4
        assert entry.last_result.status_code == 500

    # -- DLQ callback invoked --

    async def test_dead_letter_callback_invoked(self) -> None:
        callback = AsyncMock()
        responses = [_server_error(self._URL)] * 4
        delivery = _make_mock_delivery(responses)
        manager = WebhookRetryManager(
            delivery,
            policy=self._FAST_POLICY,
            on_dead_letter=callback,
        )

        await manager.deliver_with_retry(self._URL, self._PAYLOAD)

        callback.assert_awaited_once()
        entry_arg = callback.call_args[0][0]
        assert isinstance(entry_arg, DeadLetterEntry)
        assert entry_arg.url == self._URL

    # -- DLQ callback error does not propagate --

    async def test_dead_letter_callback_error_suppressed(self) -> None:
        callback = AsyncMock(side_effect=RuntimeError("callback boom"))
        responses = [_server_error(self._URL)] * 4
        delivery = _make_mock_delivery(responses)
        manager = WebhookRetryManager(
            delivery,
            policy=self._FAST_POLICY,
            on_dead_letter=callback,
        )

        # Should not raise
        result = await manager.deliver_with_retry(self._URL, self._PAYLOAD)
        assert result.success is False
        assert len(manager.dead_letters) == 1

    # -- Retry count matches policy --

    async def test_total_attempts_equals_one_plus_max_retries(self) -> None:
        policy = RetryPolicy(max_retries=2, base_delay=0.0, max_delay=0.0, rate_per_second=1000.0)
        responses = [_server_error(self._URL)] * 3
        delivery = _make_mock_delivery(responses)
        manager = WebhookRetryManager(delivery, policy=policy)

        await manager.deliver_with_retry(self._URL, self._PAYLOAD)

        assert delivery.deliver.call_count == 3  # 1 + 2

    # -- Multiple DLQ entries --

    async def test_multiple_dlq_entries_accumulated(self) -> None:
        policy = RetryPolicy(max_retries=0, base_delay=0.0, max_delay=0.0, rate_per_second=1000.0)
        delivery = _make_mock_delivery(
            [
                _server_error(self._URL),
                _server_error(self._URL),
            ]
        )
        manager = WebhookRetryManager(delivery, policy=policy)

        await manager.deliver_with_retry(self._URL, self._PAYLOAD)
        await manager.deliver_with_retry(self._URL, self._PAYLOAD)

        assert len(manager.dead_letters) == 2

    # -- Rate limiting --

    async def test_rate_limiting_delays_delivery(self) -> None:
        # Use 1 req/sec to make rate limiting observable
        policy = RetryPolicy(max_retries=0, base_delay=0.0, max_delay=0.0, rate_per_second=1.0)
        delivery = _make_mock_delivery([_ok_result(self._URL)] * 3)
        manager = WebhookRetryManager(delivery, policy=policy)

        # First call should go through immediately (bucket starts full)
        r1 = await manager.deliver_with_retry(self._URL, self._PAYLOAD)
        assert r1.success is True

        # Verify the bucket exists for this URL
        assert self._URL in manager._url_buckets

    # -- Dead letter entry attributes --

    async def test_dead_letter_entry_has_created_at(self) -> None:
        responses = [_server_error(self._URL)] * 4
        delivery = _make_mock_delivery(responses)
        manager = WebhookRetryManager(delivery, policy=self._FAST_POLICY)

        await manager.deliver_with_retry(self._URL, self._PAYLOAD)

        entry = manager.dead_letters[0]
        assert entry.created_at > 0

    async def test_dead_letters_capped_at_max(self) -> None:
        """Emit 1001 dead letters; storage length stays exactly MAX_DEAD_LETTERS."""
        # Each deliver_with_retry uses 4 attempts (1 + max_retries) then DLQ; need 1001 * 4 responses.
        responses = [_server_error(self._URL, 500)] * 4 * (MAX_DEAD_LETTERS + 1)
        delivery = _make_mock_delivery(responses)
        manager = WebhookRetryManager(delivery, policy=self._FAST_POLICY)
        for _ in range(MAX_DEAD_LETTERS + 1):
            await manager.deliver_with_retry(self._URL, self._PAYLOAD)
        assert len(manager.dead_letters) == MAX_DEAD_LETTERS
