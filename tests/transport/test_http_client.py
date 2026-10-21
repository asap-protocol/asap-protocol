"""Tests for HTTP transport versioning on ``ASAPClient`` (ASAP-Version header)."""

from __future__ import annotations

import json

import httpx
import pytest

from asap.models.constants import ASAP_SUPPORTED_TRANSPORT_VERSIONS, ASAP_VERSION_HEADER
from asap.models.enums import TaskStatus
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest, TaskResponse
from asap.transport.client import ASAPClient


def _echo_response_json(for_envelope: Envelope) -> dict[str, object]:
    # Per ASAP protocol semantics (see handlers.py + testing/mocks.py), a
    # response's correlation_id MUST echo the REQUEST envelope's id, not its
    # correlation_id field. Binding the client (B6/BUG #6) enforces this.
    env = Envelope(
        asap_version=for_envelope.asap_version,
        sender=for_envelope.recipient,
        recipient=for_envelope.sender,
        payload_type="task.response",
        correlation_id=for_envelope.id,
        payload=TaskResponse(
            task_id="task-version",
            status=TaskStatus.COMPLETED,
            result={},
        ).model_dump(),
    )
    return {
        "jsonrpc": "2.0",
        "result": {"envelope": env.model_dump(mode="json")},
        "id": "req-1",
    }


@pytest.mark.asyncio
async def test_version_client_sends_default_supported_versions_header() -> None:
    """Default client sends comma-separated supported wire versions on POST /asap."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/asap"
        expected = ", ".join(sorted(ASAP_SUPPORTED_TRANSPORT_VERSIONS, reverse=True))
        assert request.headers.get(ASAP_VERSION_HEADER.lower()) == expected
        payload_request = json.loads(request.content.decode("utf-8"))
        inc = Envelope.model_validate(payload_request["params"]["envelope"])
        return httpx.Response(
            200,
            json=_echo_response_json(inc),
            headers={ASAP_VERSION_HEADER: "2.2"},
        )

    req = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:server",
        payload_type="task.request",
        correlation_id="corr-version-1",
        payload=TaskRequest(
            conversation_id="c1", skill_id="echo", input={"message": "x"}
        ).model_dump(),
    )

    async with ASAPClient(
        "http://127.0.0.1:8000",
        require_https=False,
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.send(req)

    assert client.last_response_asap_version == "2.2"


@pytest.mark.asyncio
async def test_version_client_respects_custom_version_order() -> None:
    """User-supplied order is preserved in the ASAP-Version header value."""
    captured: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get(ASAP_VERSION_HEADER.lower()))
        payload_request = json.loads(request.content.decode("utf-8"))
        inc = Envelope.model_validate(payload_request["params"]["envelope"])
        return httpx.Response(
            200,
            json=_echo_response_json(inc),
            headers={ASAP_VERSION_HEADER: "2.1"},
        )

    req = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:server",
        payload_type="task.request",
        correlation_id="corr-version-2",
        payload=TaskRequest(
            conversation_id="c2", skill_id="echo", input={"message": "y"}
        ).model_dump(),
    )

    async with ASAPClient(
        "http://127.0.0.1:8000",
        require_https=False,
        supported_transport_versions=("2.1", "2.2"),
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.send(req)

    assert captured == ["2.1, 2.2"]
    assert client.last_response_asap_version == "2.1"


def test_version_client_rejects_unknown_supported_transport_versions() -> None:
    """Invalid wire versions in supported_transport_versions raise ValueError."""
    with pytest.raises(ValueError, match="Unsupported ASAP transport versions"):
        ASAPClient(
            "http://127.0.0.1:8000",
            require_https=False,
            supported_transport_versions=("9.9",),
        )


def test_version_client_rejects_empty_supported_transport_versions() -> None:
    """An empty version list must fail at construction, not send a blank header."""
    with pytest.raises(ValueError, match="must be non-empty"):
        ASAPClient(
            "http://127.0.0.1:8000",
            require_https=False,
            supported_transport_versions=(),
        )
