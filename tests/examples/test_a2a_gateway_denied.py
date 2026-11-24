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
from starlette.requests import Request

from asap.adapters.a2a import (
    A2AInvalidSignatureError,
    A2ATrustConfig,
    AgentCardIngestor,
    CardSigner,
)
from asap.auth.external_tokens import ExternalTokenConfig
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from examples.a2a_gateway.client import call_skill, pinned_card_verifier
from examples.a2a_gateway.demo_agent import (
    DEMO_SKILLS,
    SkillHandler,
    demo_card_inputs,
    demo_manifest,
)
from examples.a2a_gateway.gateway import SkillCallContextBuilder, create_gateway_app
from tests.examples.test_a2a_gateway import (
    AUDIENCE,
    CARD_KEY,
    CARD_KID,
    ISSUER,
    ORIGIN,
    build_gateway,
)
from tests.auth.local_issuer import LocalIssuer

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


async def test_an_unreachable_key_source_is_503() -> None:
    idp = LocalIssuer("https://idp.example")
    issuer_entry = idp.config_entry(
        AUDIENCE,
        key_source={"kind": "jwks_uri", "jwks_uri": "https://idp.example/jwks"},
        grant_rules=[{"scope": "demo:echo", "capability": "echo"}],
    )
    failing_idp = httpx.MockTransport(lambda request: httpx.Response(500))
    app = create_gateway_app(
        demo_manifest(ORIGIN),
        demo_card_inputs(ORIGIN),
        signers=[CardSigner(key=CARD_KEY, kid=CARD_KID)],
        external_tokens=ExternalTokenConfig.model_validate({"issuers": [issuer_entry]}),
        skills=DEMO_SKILLS,
        dns_resolver=_public_dns,
        transport=failing_idp,
    )
    token = idp.mint(idp.claims(AUDIENCE, scope="demo:echo"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as http:
        response = await http.post("/a2a", json={}, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert response.json() == _body("temporarily_unavailable", "Authentication service unavailable")
    assert "www-authenticate" not in response.headers


def test_the_sdk_call_context_never_holds_the_token() -> None:
    headers = [
        (b"authorization", b"Bearer secret-token"),
        (b"cookie", b"s=1"),
        (b"a2a-version", b"1.0"),
    ]
    request = Request({"type": "http", "method": "POST", "path": "/a2a", "headers": headers})

    context = SkillCallContextBuilder().build(request)

    assert context.state["headers"] == {"a2a-version": "1.0"}
    assert "secret-token" not in repr(context.state)


async def test_the_asap_endpoint_beside_the_card_runs_no_handler(
    http: httpx.AsyncClient, calls: list[str]
) -> None:
    request = TaskRequest(conversation_id="c-1", skill_id="echo", input={"text": "hi"})
    envelope = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:a2a-gateway-demo",
        payload_type="task.request",
        payload=request.model_dump(),
    )
    body = {"jsonrpc": "2.0", "id": 1, "method": "asap.send"}

    response = await http.post(
        "/asap", json=body | {"params": {"envelope": envelope.model_dump(mode="json")}}
    )

    assert "result" not in response.json() and "error" in response.json()
    assert calls == []
