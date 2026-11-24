"""Test-only compliance mode of the served gateway.

``POST /compliance/probe-tokens`` hands out fresh access tokens in the JSON
shape of ``examples/mcp_auth_http``, so the ``asap-compliance`` provider reads
it unchanged. Without ``--compliance`` the path does not exist.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from asap_compliance.validators.probe_tokens import (
    ComplianceEndpointTokenProvider,
    ProbeTokenKind,
)
from examples.a2a_gateway import serve
from examples.a2a_gateway.compliance import COMPLIANCE_PROBE_PATH
from examples.a2a_gateway.skill_call import SKILL_SELECTION_KEY

ORIGIN = "http://127.0.0.1:8788"


async def _post(
    app: FastAPI,
    path: str,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    sent = {"A2A-Version": "1.0", **(headers or {})}
    if token is not None:
        sent["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as http:
        return await http.post(path, headers=sent, **kwargs)


def _call(skill_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    message = {
        "role": "ROLE_USER",
        "messageId": str(uuid4()),
        "parts": [{"data": arguments}],
        "metadata": {SKILL_SELECTION_KEY: {"skill_id": skill_id}},
    }
    return {"jsonrpc": "2.0", "id": 7, "method": "SendMessage", "params": {"message": message}}


class _RecordingRunner:
    def __init__(self) -> None:
        self.apps: list[FastAPI] = []

    def __call__(self, app: FastAPI, host: str, port: int) -> None:
        self.apps.append(app)


class TestOffByDefault:
    async def test_without_the_flag_the_probe_path_does_not_exist(self) -> None:
        served = serve.build_served_gateway(ORIGIN)

        response = await _post(served.app, COMPLIANCE_PROBE_PATH)

        assert response.status_code == 404
        assert "external" not in response.text

    @pytest.mark.parametrize(("argv", "status"), [([], 404), (["--compliance"], 200)])
    async def test_only_the_command_line_flag_turns_it_on(
        self, argv: list[str], status: int
    ) -> None:
        runner = _RecordingRunner()

        serve.main(argv, run_server=runner)

        assert (await _post(runner.apps[0], COMPLIANCE_PROBE_PATH)).status_code == status

    async def test_a_non_local_origin_cannot_reach_the_probe_path(self) -> None:
        served = serve.build_served_gateway(ORIGIN, compliance=True)

        response = await _post(
            served.app, COMPLIANCE_PROBE_PATH, headers={"Origin": "https://evil.example"}
        )

        assert response.status_code == 403
        assert "external" not in response.text


class TestProbeTokenSet:
    async def test_the_set_has_the_shape_of_the_mcp_example_and_the_card_key(self) -> None:
        served = serve.build_served_gateway(ORIGIN, compliance=True)

        response = await _post(served.app, COMPLIANCE_PROBE_PATH)
        probe = response.json()

        assert response.headers["cache-control"] == "no-store"
        assert (probe["profile"], probe["test_only"]) == ("a2a-bridge", True)
        assert (probe["a2a_url"], probe["skill_id"]) == (f"{ORIGIN}/a2a", "greet")
        assert probe["allowed_arguments"] == {"name": "Ada", "language": "en"}
        assert probe["constraint_violation_arguments"] == {"name": "Ada", "language": "fr"}
        assert set(probe["external"]) == {"valid", "out_of_grant", "wrong_audience"}
        assert probe["card_key"] == {"kid": served.card_kid, "jwk": served.card_public_jwk}

    async def test_each_token_gets_its_answer_round_after_round(self) -> None:
        served = serve.build_served_gateway(ORIGIN, compliance=True)
        seen: set[str] = set()

        for _ in range(2):
            probe = (await _post(served.app, COMPLIANCE_PROBE_PATH)).json()
            external = probe["external"]
            allowed = _call("greet", probe["allowed_arguments"])
            for _use in range(2):
                ok = await _post(served.app, "/a2a", token=external["valid"], json=allowed)
                assert ok.json()["result"]["message"]["parts"][0]["data"] == {
                    "greeting": "Hello, Ada!"
                }
            denied = await _post(served.app, "/a2a", token=external["out_of_grant"], json=allowed)
            elsewhere = await _post(
                served.app, "/a2a", token=external["wrong_audience"], json=allowed
            )
            violation = _call("greet", probe["constraint_violation_arguments"])
            broken = await _post(served.app, "/a2a", token=external["valid"], json=violation)
            assert (denied.status_code, elsewhere.status_code, broken.status_code) == (
                403,
                401,
                403,
            )
            assert broken.json()["error_description"] == "asap:constraint_violation"
            assert seen.isdisjoint(external.values())
            seen.update(external.values())

    async def test_the_compliance_endpoint_token_provider_reads_it_unchanged(self) -> None:
        served = serve.build_served_gateway(ORIGIN, compliance=True)
        provider = ComplianceEndpointTokenProvider(
            f"{ORIGIN}{COMPLIANCE_PROBE_PATH}", http_transport=httpx.ASGITransport(app=served.app)
        )

        valid = await provider.probe_token(ProbeTokenKind.EXTERNAL)
        out_of_grant = await provider.probe_token(ProbeTokenKind.EXTERNAL_OUT_OF_GRANT)

        allowed = _call("greet", {"name": "Ada", "language": "en"})
        assert (await _post(served.app, "/a2a", token=valid, json=allowed)).status_code == 200
        assert (
            await _post(served.app, "/a2a", token=out_of_grant, json=allowed)
        ).status_code == 403
