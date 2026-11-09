"""Lock ASAP-Version negotiation to the supported token set."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from asap.models.constants import ASAP_DEFAULT_TRANSPORT_VERSION
from asap.models.entities import Manifest
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.transport.jsonrpc import VERSION_INCOMPATIBLE
from asap.transport.rate_limit import ASAPRateLimiter
from asap.transport.server import create_app

_LOOKALIKES = ("2.20", "2.2.0", "2.2extra", "v2.2")


def _request_body(request_id: str) -> dict[str, object]:
    envelope = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:test-server",
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id=f"conv-{request_id}",
            skill_id="echo",
            input={"message": "hello"},
        ).model_dump(),
    )
    return {
        "jsonrpc": "2.0",
        "method": "asap.send",
        "params": {"envelope": envelope.model_dump(mode="json")},
        "id": request_id,
    }


@pytest.fixture
def version_client(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
) -> Iterator[TestClient]:
    """App client with the suite's isolated limiter so version probes are not 429s."""
    app = create_app(sample_manifest, rate_limit="999999/minute")
    if isolated_rate_limiter is not None:
        app.state.limiter = isolated_rate_limiter
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("header", _LOOKALIKES)
def test_transport_version_lookalikes_are_incompatible(
    version_client: TestClient,
    header: str,
) -> None:
    """Tokens that merely contain 2.1 or 2.2 are not negotiated."""
    response = version_client.post(
        "/asap",
        json=_request_body(f"version-{header}"),
        headers={"ASAP-Version": header},
    )
    assert response.status_code == 200
    assert response.headers.get("ASAP-Version") == ASAP_DEFAULT_TRANSPORT_VERSION
    payload = response.json()
    assert "result" not in payload
    assert payload["error"]["code"] == VERSION_INCOMPATIBLE
    assert payload["error"]["data"]["requested"] == header


def test_transport_version_skips_lookalike_before_supported_token(
    version_client: TestClient,
) -> None:
    """``2.20, 2.2`` negotiates 2.2. The first token is not a prefix match."""
    response = version_client.post(
        "/asap",
        json=_request_body("version-skip"),
        headers={"ASAP-Version": "2.20, 2.2"},
    )
    assert response.status_code == 200
    assert response.headers.get("ASAP-Version") == "2.2"
    assert "result" in response.json()
