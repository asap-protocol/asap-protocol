"""The served gateway of ``examples/a2a_gateway/serve.py``.

The served app is driven in process over ``httpx.ASGITransport``, and the command
runs with a recording server runner, so no test opens a port.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pytest import CaptureFixture

from asap.adapters.a2a import (
    A2AKeyResolver,
    A2ATrustConfig,
    AgentCard,
    PinnedCardKey,
    read_asap_extension,
    verify_agent_card_for_agent,
)
from examples.a2a_gateway import serve
from examples.a2a_gateway.demo_agent import A2A_PATH, DEMO_AGENT_URN
from examples.a2a_gateway.skill_call import SKILL_SELECTION_KEY

ORIGIN = "http://127.0.0.1:8788"


@pytest.fixture
def served() -> serve.ServedGateway:
    return serve.build_served_gateway(ORIGIN)


async def _request(
    app: FastAPI, method: str, path: str, headers: dict[str, str], **kwargs: Any
) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as http:
        return await http.request(method, path, headers=headers, **kwargs)


def _greet_call() -> dict[str, Any]:
    message = {
        "role": "ROLE_USER",
        "messageId": str(uuid4()),
        "parts": [{"data": {"name": "Ada", "language": "en"}}],
        "metadata": {SKILL_SELECTION_KEY: {"skill_id": "greet"}},
    }
    return {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": message}}


class TestServedGateway:
    async def test_the_card_names_the_served_origin_and_the_demo_agent(
        self, served: serve.ServedGateway
    ) -> None:
        response = await _request(served.app, "GET", "/.well-known/agent-card.json", {})
        card = AgentCard.model_validate(response.json())

        assert served.origin == ORIGIN
        assert [interface.url for interface in card.supported_interfaces] == [f"{ORIGIN}/a2a"]
        extension = read_asap_extension(card)
        assert extension is not None and extension.agent_urn == DEMO_AGENT_URN

    async def test_the_card_verifies_under_the_served_key_for_the_demo_agent(
        self, served: serve.ServedGateway
    ) -> None:
        response = await _request(served.app, "GET", "/.well-known/agent-card.json", {})
        pinned = PinnedCardKey(jwk=served.card_public_jwk, agents=(DEMO_AGENT_URN,))
        trust = A2ATrustConfig(pinned_keys={served.card_kid: pinned})

        card = AgentCard.model_validate(response.json())
        result = await verify_agent_card_for_agent(card, DEMO_AGENT_URN, A2AKeyResolver(trust))

        assert result.verified
        assert "d" not in served.card_public_jwk

    async def test_a_token_of_the_served_issuer_calls_greet(
        self, served: serve.ServedGateway
    ) -> None:
        audience = f"{ORIGIN}{A2A_PATH}"
        token = served.issuer.mint(served.issuer.claims(audience, scope="demo:greet"))
        headers = {"A2A-Version": "1.0", "Authorization": f"Bearer {token}"}

        response = await _request(served.app, "POST", A2A_PATH, headers, json=_greet_call())

        assert response.status_code == 200
        assert response.json()["result"]["message"]["parts"] == [
            {"data": {"greeting": "Hello, Ada!"}}
        ]


class TestLoopbackOnly:
    """Local ``Host`` and ``Origin`` only, as in ``examples/mcp_auth_http``."""

    @pytest.mark.parametrize(
        "host", ["127.0.0.1:8788", "localhost:8788", "[::1]:8788", "127.0.0.2"]
    )
    async def test_a_loopback_host_header_passes(
        self, served: serve.ServedGateway, host: str
    ) -> None:
        response = await _request(served.app, "GET", "/.well-known/agent-card.json", {"Host": host})

        assert response.status_code == 200

    @pytest.mark.parametrize(
        "host",
        ["evil.example", "evil.example:8788", "192.168.1.10:8788", "127.0.0.1.evil.example", ""],
    )
    async def test_another_host_header_is_refused(
        self, served: serve.ServedGateway, host: str
    ) -> None:
        response = await _request(served.app, "GET", "/.well-known/agent-card.json", {"Host": host})

        assert response.status_code == 403
        assert "evil" not in response.text

    @pytest.mark.parametrize("origin", ["http://localhost:3000", ORIGIN, "https://[::1]"])
    async def test_a_loopback_origin_passes(self, served: serve.ServedGateway, origin: str) -> None:
        response = await _request(
            served.app, "GET", "/.well-known/agent-card.json", {"Origin": origin}
        )

        assert response.status_code == 200

    @pytest.mark.parametrize(
        "origin", ["https://evil.example", "null", "http://127.0.0.1.evil.example"]
    )
    async def test_another_origin_is_refused_before_the_token_check(
        self, served: serve.ServedGateway, origin: str
    ) -> None:
        response = await _request(
            served.app, "POST", A2A_PATH, {"Origin": origin}, json=_greet_call()
        )

        assert response.status_code == 403
        assert "www-authenticate" not in response.headers


class _RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[FastAPI, str, int]] = []

    def __call__(self, app: FastAPI, host: str, port: int) -> None:
        self.calls.append((app, host, port))


class TestServeCommand:
    @pytest.mark.parametrize(
        "host", ["0.0.0.0", "::", "192.168.1.10", "localhost", "gateway.example"]
    )
    def test_a_host_that_is_not_a_loopback_ip_stops_before_binding(self, host: str) -> None:
        runner = _RecordingRunner()

        with pytest.raises(SystemExit):
            serve.main(["--host", host], run_server=runner)

        assert runner.calls == []

    @pytest.mark.parametrize("port", ["0", "65536", "http"])
    def test_a_port_outside_1_to_65535_stops_before_binding(self, port: str) -> None:
        runner = _RecordingRunner()

        with pytest.raises(SystemExit):
            serve.main(["--port", port], run_server=runner)

        assert runner.calls == []

    def test_the_default_serves_127_0_0_1_port_8788(self, capsys: CaptureFixture[str]) -> None:
        runner = _RecordingRunner()

        serve.main([], run_server=runner)

        [(app, host, port)] = runner.calls
        assert (host, port) == ("127.0.0.1", 8788)
        assert isinstance(app, FastAPI)
        assert "http://127.0.0.1:8788" in capsys.readouterr().out

    def test_an_ipv6_loopback_origin_is_bracketed(self) -> None:
        runner = _RecordingRunner()

        serve.main(["--host", "::1", "--port", "9000"], run_server=runner)

        assert serve.served_origin("::1", 9000) == "http://[::1]:9000"
        assert [call[1:] for call in runner.calls] == [("::1", 9000)]
