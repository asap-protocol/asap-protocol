"""Tests for JSON-RPC batch operations.

Covers server-side batch handling (POST /asap with array body) and
the client ``batch()`` method that sends a single HTTP call with an
array body per JSON-RPC 2.0 batch specification.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from httpx import ASGITransport, AsyncClient

from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.transport.jsonrpc import (
    ASAP_METHOD,
    INVALID_REQUEST,
    PARSE_ERROR,
)
from asap.transport.rate_limit import create_test_limiter, get_remote_address
from asap.transport.server import create_app

if TYPE_CHECKING:
    from asap.transport.rate_limit import ASAPRateLimiter

from .conftest import NoRateLimitTestBase, TEST_RATE_LIMIT_DEFAULT


def _make_manifest() -> Manifest:
    return Manifest(
        id="urn:asap:agent:batch-test",
        name="Batch Test Agent",
        version="1.0.0",
        description="Agent for batch tests",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo input")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


def _make_rpc_request(envelope: Envelope, req_id: str | int = "r1") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": ASAP_METHOD,
        "params": {"envelope": envelope.model_dump(mode="json")},
        "id": req_id,
    }


def _make_envelope(skill_id: str = "echo", sender: str = "urn:asap:agent:client") -> Envelope:
    return Envelope(
        asap_version="0.1",
        sender=sender,
        recipient="urn:asap:agent:batch-test",
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id="conv-batch",
            skill_id=skill_id,
            input={"message": "batch-test"},
        ).model_dump(),
    )


class TestBatchServer(NoRateLimitTestBase):
    """Server-side JSON-RPC batch tests."""

    @pytest.fixture()
    def app(self, disable_rate_limiting: ASAPRateLimiter) -> Any:
        """Create app with isolated rate limiter and small max_batch_size for tests."""
        manifest = _make_manifest()
        app_instance = create_app(
            manifest,
            rate_limit=TEST_RATE_LIMIT_DEFAULT,
            max_batch_size=5,
        )
        app_instance.state.limiter = disable_rate_limiting
        return app_instance

    @pytest.fixture()
    def transport(self, app: Any) -> ASGITransport:
        return ASGITransport(app=app)

    async def test_valid_batch_returns_array(self, transport: ASGITransport) -> None:
        """A batch of valid requests returns an array of JSON-RPC responses."""
        env1 = _make_envelope()
        env2 = _make_envelope()
        batch_body = [_make_rpc_request(env1, "r1"), _make_rpc_request(env2, "r2")]

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/asap", content=json.dumps(batch_body))

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        for item in data:
            assert item["jsonrpc"] == "2.0"
            assert "result" in item or "error" in item

    async def test_empty_batch_returns_error(self, transport: ASGITransport) -> None:
        """An empty batch array returns a single INVALID_REQUEST error."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/asap", content=json.dumps([]))

        assert resp.status_code == 200
        data = resp.json()
        assert data["error"]["code"] == INVALID_REQUEST
        assert "empty batch" in data["error"]["data"]["reason"]

    async def test_oversized_batch_returns_error(self, app: Any, transport: ASGITransport) -> None:
        """A batch one past ``app.state.max_batch_size`` returns a single error."""
        env = _make_envelope()
        over_limit = int(app.state.max_batch_size) + 1
        batch_body = [_make_rpc_request(env, f"r{i}") for i in range(over_limit)]

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/asap", content=json.dumps(batch_body))

        assert resp.status_code == 200
        data = resp.json()
        assert data["error"]["code"] == INVALID_REQUEST
        assert "exceeds max" in data["error"]["data"]["reason"]

    async def test_batch_at_max_size_returns_one_response_per_item(
        self,
        app: Any,
        transport: ASGITransport,
    ) -> None:
        """A batch of exactly ``app.state.max_batch_size`` returns one response per item."""
        env = _make_envelope()
        limit = int(app.state.max_batch_size)
        batch_body = [_make_rpc_request(env, f"r{i}") for i in range(limit)]

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/asap", content=json.dumps(batch_body))

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == limit
        assert all(item["jsonrpc"] == "2.0" for item in data)

    async def test_non_dict_items_return_errors(self, transport: ASGITransport) -> None:
        """Non-dict items in the batch produce INVALID_REQUEST for those entries."""
        env = _make_envelope()
        batch_body: list[Any] = [42, "bad", _make_rpc_request(env, "good")]

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/asap", content=json.dumps(batch_body))

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 3
        assert data[0]["error"]["code"] == INVALID_REQUEST
        assert data[1]["error"]["code"] == INVALID_REQUEST
        assert "result" in data[2] or "error" in data[2]

    async def test_single_request_still_works(self, transport: ASGITransport) -> None:
        """A single (non-array) JSON-RPC request continues to work normally."""
        env = _make_envelope()
        body = _make_rpc_request(env, "single")

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/asap", json=body)

        assert resp.status_code == 200
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        assert "result" in data or "error" in data

    async def test_invalid_json_returns_parse_error(self, transport: ASGITransport) -> None:
        """Completely invalid JSON returns PARSE_ERROR."""
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/asap",
                content=b"{broken json",
                headers={"content-type": "application/json"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["error"]["code"] == PARSE_ERROR


class TestBatchRateLimiting:
    """Batch requests consume N rate-limit hits."""

    def _limited_app(self, limits: list[str], *, max_batch_size: int = 5) -> Any:
        """Build an app whose route limiter uses an isolated in-memory store."""
        limiter = create_test_limiter(limits, key_func=get_remote_address)
        app = create_app(
            _make_manifest(),
            rate_limit="100000/minute",
            max_batch_size=max_batch_size,
        )
        app.state.limiter = limiter
        return app

    async def test_empty_batch_does_not_spend_quota(self) -> None:
        """An empty batch is rejected before check_n, so the next call still fits."""
        app = self._limited_app(["1/minute"])
        env = _make_envelope()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            rejected = await client.post("/asap", content=json.dumps([]))
            allowed = await client.post("/asap", json=_make_rpc_request(env, "ok"))

        assert rejected.status_code == 200
        assert "empty batch" in rejected.json()["error"]["data"]["reason"]
        assert allowed.status_code == 200

    async def test_oversized_batch_does_not_spend_quota(self) -> None:
        """A batch past max_batch_size is rejected before check_n."""
        app = self._limited_app(["1/minute"], max_batch_size=5)
        env = _make_envelope()
        oversized = [_make_rpc_request(env, f"r{index}") for index in range(6)]
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            rejected = await client.post("/asap", content=json.dumps(oversized))
            allowed = await client.post("/asap", json=_make_rpc_request(env, "ok"))

        assert rejected.status_code == 200
        assert "exceeds max" in rejected.json()["error"]["data"]["reason"]
        assert allowed.status_code == 200

    async def test_invalid_utf8_body_is_a_parse_error_and_debits_one_hit(self) -> None:
        """Non-UTF-8 bodies are parse errors and consume one limiter hit, not zero or two."""
        app = self._limited_app(["1/minute"])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            bad = await client.post(
                "/asap",
                content=b"\xff\xfe\x00",
                headers={"content-type": "application/json"},
            )
            again = await client.post("/asap", json=_make_rpc_request(_make_envelope(), "next"))

        assert bad.status_code == 200
        assert bad.json()["error"]["code"] == PARSE_ERROR
        assert "Invalid UTF-8" in bad.text
        assert again.status_code == 429

    async def test_batch_rate_limit_check_n(self) -> None:
        """check_n on the rate limiter is invoked for batch length."""
        manifest = _make_manifest()
        limiter = create_test_limiter(["3/minute"], key_func=get_remote_address)

        app_instance = create_app(
            manifest,
            rate_limit="100000/minute",
            max_batch_size=10,
        )
        app_instance.state.limiter = limiter

        env = _make_envelope()
        batch_body = [_make_rpc_request(env, f"r{i}") for i in range(3)]

        transport = ASGITransport(app=app_instance)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/asap", content=json.dumps(batch_body))
        assert resp.status_code == 200

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/asap", content=json.dumps(batch_body))
        assert resp.status_code == 429


class TestBatchClient(NoRateLimitTestBase):
    """Client-side batch() method tests."""

    @pytest.fixture()
    def app(self, disable_rate_limiting: ASAPRateLimiter) -> Any:
        manifest = _make_manifest()
        app_instance = create_app(manifest, rate_limit=TEST_RATE_LIMIT_DEFAULT)
        app_instance.state.limiter = disable_rate_limiting
        return app_instance

    async def test_client_batch_sends_array(self, app: Any) -> None:
        """ASAPClient.batch() sends a single HTTP request with JSON array body."""
        from asap.transport.client import ASAPClient

        transport = ASGITransport(app=app)
        async with ASAPClient(
            "http://test",
            require_https=False,
            transport=transport,
        ) as client:
            env1 = _make_envelope()
            env2 = _make_envelope()
            results = await client.batch([env1, env2])

        assert len(results) == 2
        for r in results:
            assert isinstance(r, Envelope)

    async def test_client_batch_empty_raises(self) -> None:
        """ASAPClient.batch() raises ValueError for empty list."""
        from asap.transport.client import ASAPClient

        transport = ASGITransport(app=create_app(_make_manifest()))
        async with ASAPClient(
            "http://test",
            require_https=False,
            transport=transport,
        ) as client:
            with pytest.raises(ValueError, match="empty"):
                await client.batch([])

    async def test_client_batch_rejects_mismatched_correlation_id(self) -> None:
        """B6/BUG #6: batch() rejects a sub-response whose correlation_id != request id.

        A malicious/buggy peer could permute batch response bodies (or ids) while
        keeping HTTP 200; the client must bind each sub-response to its originating
        request by correlation_id and raise ProtocolCorrelationError on mismatch,
        mirroring send() and the WebSocket recv loop.
        """
        import httpx

        from asap.models.enums import TaskStatus
        from asap.models.payloads import TaskResponse
        from asap.transport.client import ASAPClient
        from asap.transport.errors import ProtocolCorrelationError

        env1 = _make_envelope()
        env2 = _make_envelope()

        def malicious_transport(request: httpx.Request) -> httpx.Response:
            # Parse the batch array the client sent so we can echo each request's
            # JSON-RPC id while returning a response whose correlation_id does NOT
            # match the request envelope id (the B6 attack).
            batch_body = json.loads(request.content)
            items: list[dict[str, Any]] = []
            for entry in batch_body:
                req_id = entry["id"]
                response_envelope = Envelope(
                    asap_version="0.1",
                    sender="urn:asap:agent:batch-test",
                    recipient="urn:asap:agent:client",
                    payload_type="task.response",
                    payload=TaskResponse(
                        task_id="task_evil",
                        status=TaskStatus.COMPLETED,
                        result={"echoed": {"message": "permuted"}},
                    ).model_dump(),
                    # Non-empty but wrong: does not match the request envelope id.
                    correlation_id="definitely-not-the-request-id",
                )
                items.append(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"envelope": response_envelope.model_dump(mode="json")},
                    }
                )
            return httpx.Response(200, json=items)

        async with ASAPClient(
            "http://test",
            require_https=False,
            transport=httpx.MockTransport(malicious_transport),
        ) as client:
            with pytest.raises(ProtocolCorrelationError) as exc_info:
                await client.batch([env1, env2])

            message = str(exc_info.value)
            assert "correlation_id" in message
            assert repr("definitely-not-the-request-id") in message

    async def test_client_batch_rejects_unknown_response_id(self) -> None:
        """B6/BUG #6: batch() rejects a sub-response whose JSON-RPC id matches no sent request.

        A peer that returns a result for an id we did not send (permutation / forgery)
        must be rejected rather than silently paired by array index.
        """
        import httpx

        from asap.models.enums import TaskStatus
        from asap.models.payloads import TaskResponse
        from asap.transport.client import ASAPClient
        from asap.transport.errors import ProtocolCorrelationError

        env1 = _make_envelope()

        def malicious_transport(request: httpx.Request) -> httpx.Response:
            response_envelope = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:batch-test",
                recipient="urn:asap:agent:client",
                payload_type="task.response",
                payload=TaskResponse(
                    task_id="task_evil",
                    status=TaskStatus.COMPLETED,
                    result={},
                ).model_dump(),
                correlation_id=env1.id,
            )
            # An id we never sent.
            return httpx.Response(
                200,
                json=[
                    {
                        "jsonrpc": "2.0",
                        "id": "forged-id-not-in-batch",
                        "result": {"envelope": response_envelope.model_dump(mode="json")},
                    }
                ],
            )

        async with ASAPClient(
            "http://test",
            require_https=False,
            transport=httpx.MockTransport(malicious_transport),
        ) as client:
            with pytest.raises(ProtocolCorrelationError) as exc_info:
                await client.batch([env1])

            # The forged JSON-RPC id appears as the expected request_id, proving
            # the unknown-id branch fired (no sent request had that id).
            assert repr("forged-id-not-in-batch") in str(exc_info.value)
