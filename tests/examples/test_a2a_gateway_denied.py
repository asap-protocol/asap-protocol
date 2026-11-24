"""Denied paths of the gateway's A2A flow.

Each refusal is checked through the ``a2a-sdk`` client: it raises
``A2AClientError``, whose ``__cause__`` holds the HTTP answer. Every case
also asserts that no skill handler ran, and that the token appears in no
answer and no log record.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from a2a.client.errors import A2AClientError
from a2a.utils.signing import InvalidSignaturesError
from fastapi import FastAPI
from joserfc.jwk import OKPKey

from asap.adapters.a2a import A2AInvalidSignatureError, A2ATrustConfig, AgentCardIngestor
from examples.a2a_gateway.client import call_skill, pinned_card_verifier
from examples.a2a_gateway.demo_agent import DEMO_SKILLS, SkillHandler
from tests.examples.test_a2a_gateway import (
    AUDIENCE,
    CARD_KEY,
    CARD_KID,
    ISSUER,
    ORIGIN,
    build_gateway,
)

_VERIFY_CARD = pinned_card_verifier(CARD_KID, CARD_KEY.public_key())
_CARD_JWK = {**OKPKey.import_key(CARD_KEY.public_key()).as_dict(), "kid": CARD_KID}
_INVALID = 'Bearer error="invalid_token", error_description="asap:invalid_token"'
_SCOPE = 'Bearer error="insufficient_scope", error_description="asap:capability_denied"'


def _body(error: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"error": error, "error_description": code, **extra}


async def _public_dns(hostname: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def app(calls: list[str]) -> FastAPI:
    def recording(name: str) -> SkillHandler:
        async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
            calls.append(name)
            return await DEMO_SKILLS[name](arguments)

        return handler

    return build_gateway(skills={name: recording(name) for name in DEMO_SKILLS})


@pytest.fixture
async def http(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as c:
        yield c


async def _refused(http: httpx.AsyncClient, token: str | None, skill_id: str, **args: Any) -> Any:
    with pytest.raises(A2AClientError) as caught:
        await call_skill(
            http, ORIGIN, verify_card=_VERIFY_CARD, token=token, skill_id=skill_id, arguments=args
        )
    assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)
    return caught.value.__cause__.response


_ECHO = ("echo", {"text": "hi"})
_GREET_EN = ("greet", {"name": "Ada", "language": "en"})
_GREET_FR = ("greet", {"name": "Ada", "language": "fr"})
_AUTH_REQUIRED = (401, "Bearer", _body("invalid_token", "asap:auth_required"))
_INVALID_TOKEN = (401, _INVALID, _body("invalid_token", "asap:invalid_token"))
_NO_GRANT = (403, _SCOPE, _body("insufficient_scope", "asap:capability_denied"))
_VIOLATION = (
    403,
    None,
    _body(
        "access_denied",
        "asap:constraint_violation",
        violations=[{"field": "language", "operator": "in"}],
    ),
)
_WRONG_AUDIENCE = {"aud": "https://other.example/a2a", "scope": "demo:echo"}


@pytest.mark.parametrize(
    ("token_claims", "token_header", "call", "expected"),
    [
        pytest.param(None, {}, _ECHO, _AUTH_REQUIRED, id="no-token"),
        pytest.param(_WRONG_AUDIENCE, {}, _ECHO, _INVALID_TOKEN, id="wrong-audience"),
        pytest.param(
            {"scope": "demo:echo"}, {"typ": "agent+jwt"}, _ECHO, _INVALID_TOKEN, id="asap-agent-jwt"
        ),
        pytest.param(
            {"scope": "demo:echo files:read"}, {}, _GREET_EN, _NO_GRANT, id="unmapped-scope"
        ),
        pytest.param({"scope": "demo:greet"}, {}, _GREET_FR, _VIOLATION, id="constraint-violation"),
    ],
)
async def test_each_refusal_has_its_status_challenge_and_asap_code_and_runs_no_skill(
    http: httpx.AsyncClient,
    calls: list[str],
    caplog: pytest.LogCaptureFixture,
    token_claims: dict[str, Any] | None,
    token_header: dict[str, Any],
    call: tuple[str, dict[str, Any]],
    expected: tuple[int, str | None, dict[str, Any]],
) -> None:
    token = None
    if token_claims is not None:
        token = ISSUER.mint({**ISSUER.claims(AUDIENCE), **token_claims}, header=token_header)
    skill_id, args = call
    status, challenge, body = expected

    with caplog.at_level("INFO"):
        response = await _refused(http, token, skill_id, **args)

    assert (response.status_code, response.json()) == (status, body)
    assert response.headers.get("www-authenticate") == challenge
    assert calls == []
    if token is not None:
        assert token not in response.text and token not in str(response.headers)
        assert token not in caplog.text


async def test_a_tampered_card_fails_both_verifiers_and_no_call_is_made(
    app: FastAPI, calls: list[str]
) -> None:
    calls_made: list[str] = []

    async def tampering(request: httpx.Request) -> httpx.Response:
        calls_made.append(request.url.path)
        response = await httpx.ASGITransport(app=app).handle_async_request(request)
        if request.url.path != "/.well-known/agent-card.json":
            return response
        await response.aread()
        card = response.json() | {"description": "A different agent"}
        return httpx.Response(200, json=card)

    transport = httpx.MockTransport(tampering)
    token = ISSUER.mint(ISSUER.claims(AUDIENCE, scope="demo:echo"))
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as http:
        with pytest.raises(InvalidSignaturesError):
            await call_skill(
                http,
                ORIGIN,
                verify_card=_VERIFY_CARD,
                token=token,
                skill_id="echo",
                arguments={"text": "hi"},
            )
    ingestor = AgentCardIngestor(
        A2ATrustConfig(pinned_keys={CARD_KID: _CARD_JWK}),
        dns_resolver=_public_dns,
        transport=transport,
    )
    with pytest.raises(A2AInvalidSignatureError):
        await ingestor.ingest(f"{ORIGIN}/.well-known/agent-card.json")
    assert "/a2a" not in calls_made and calls == []
