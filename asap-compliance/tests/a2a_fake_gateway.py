"""Fake A2A gateway for the ``a2a-bridge`` profile tests.

It answers like ``examples/a2a_gateway`` served with ``--compliance``: the
card at the well-known path, the ASAP manifest beside it, a probe endpoint, and
``POST /a2a`` in the layers of its README's refusal table. Each ``Fault``
breaks one behavior, so each profile check has a passing and a failing server.
"""

from __future__ import annotations

import base64
import json
import secrets
from enum import StrEnum
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc.jwk import OKPKey

from asap.adapters.a2a import (
    ASAP_EXTENSION_URI,
    AgentCard,
    AgentCardInputs,
    AgentInterface,
    CardSigner,
    build_signed_agent_card,
    sign_agent_card,
)
from asap.models.entities import Capability, Endpoint, Manifest, Skill

BASE_URL = "https://gateway.example"
AGENT_URN = "urn:asap:agent:a2a-gateway-demo"
CARD_KID = "gateway-card-1"
PROBE_TOKENS_PATH = "/compliance/probe-tokens"
CARD_KEY = Ed25519PrivateKey.generate()
CARD_PUBLIC_JWK = {**OKPKey.import_key(CARD_KEY.public_key()).as_dict(), "kid": CARD_KID}
OTHER_AGENT_URN = "urn:asap:agent:someone-else"
_A2A_URL = f"{BASE_URL}/a2a"
# What an A2A v0.3 card looks like: a top-level url and protocolVersion.
_V03_CARD = {"name": "Old", "url": _A2A_URL, "protocolVersion": "0.3.0", "version": "1.0.0"}


class Fault(StrEnum):
    """One way to break the gateway's behavior."""

    NO_CARD = "no_card"
    CARD_NOT_JSON = "card_not_json"
    CARD_V03 = "card_v03"
    UNSIGNED_CARD = "unsigned_card"
    TAMPERED_CARD = "tampered_card"
    # Signed with the key that the checker trusts for AGENT_URN, for another agent URN.
    CARD_FOR_ANOTHER_AGENT = "card_for_another_agent"
    CARD_WITHOUT_EXTENSION = "card_without_extension"
    CARD_INTERFACE_ELSEWHERE = "card_interface_elsewhere"
    NO_MANIFEST = "no_manifest"
    MANIFEST_ERROR = "manifest_error"
    MANIFEST_OF_ANOTHER_VERSION = "manifest_of_another_version"
    AUTH_REQUIRED_AS_RPC_ERROR = "auth_required_as_rpc_error"
    INVALID_TOKEN_AS_403 = "invalid_token_as_403"
    ACCEPTS_BAD_SIGNATURE = "accepts_bad_signature"
    IGNORES_AUDIENCE = "ignores_audience"
    REFUSES_VALID_TOKENS = "refuses_valid_tokens"
    SINGLE_USE_TOKENS = "single_use_tokens"
    ANSWERS_200_TO_EVERYTHING = "answers_200_to_everything"
    GRANTS_EVERYTHING = "grants_everything"
    DENIAL_WITHOUT_CHALLENGE = "denial_without_challenge"
    IGNORES_CONSTRAINTS = "ignores_constraints"
    CONSTRAINT_WITH_CHALLENGE = "constraint_with_challenge"
    RUNS_UNDECLARED_SKILL = "runs_undeclared_skill"
    NO_WWW_AUTHENTICATE = "no_www_authenticate"
    ECHOES_TOKEN_IN_BODY = "echoes_token_in_body"
    ECHOES_TOKEN_IN_HEADER = "echoes_token_in_header"


def demo_manifest(agent_urn: str = AGENT_URN, version: str = "1.0.0") -> Manifest:
    """The manifest of ``examples/a2a_gateway``, at :data:`BASE_URL`."""
    skills = [
        Skill(id="echo", description="Return the text it receives"),
        Skill(id="greet", description="Greet a person by name"),
    ]
    return Manifest(
        id=agent_urn,
        name="A2A Gateway Demo",
        version=version,
        description="An ASAP agent reached through a reference A2A v1.0 gateway",
        capabilities=Capability(skills=skills),
        endpoints=Endpoint(asap=f"{BASE_URL}/asap"),
    )


def signed_card(
    manifest: Manifest | None = None, *, interface_url: str = _A2A_URL
) -> dict[str, Any]:
    """The JSON of a card signed by :data:`CARD_KEY`, as ``create_app`` serves it."""
    interface = AgentInterface(
        url=interface_url, protocol_binding="JSONRPC", protocol_version="1.0"
    )
    inputs = AgentCardInputs(
        interfaces=[interface],
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skill_tags={"echo": ["demo"], "greet": ["demo"]},
    )
    signers = [CardSigner(key=CARD_KEY, kid=CARD_KID)]
    card = build_signed_agent_card(manifest or demo_manifest(), inputs, signers=signers).card
    return card.model_dump(mode="json")


def _signed_without_extension(card: dict[str, Any]) -> dict[str, Any]:
    body = {key: value for key, value in card.items() if key != "signatures"}
    body["capabilities"] = {"streaming": False}
    unsigned = AgentCard.model_validate(body)
    return sign_agent_card(unsigned, CARD_KEY, kid=CARD_KID).model_dump(mode="json")


_CARD = signed_card()
_CARDS_BY_FAULT: dict[Fault, dict[str, Any]] = {
    Fault.CARD_V03: _V03_CARD,
    Fault.UNSIGNED_CARD: {key: value for key, value in _CARD.items() if key != "signatures"},
    Fault.TAMPERED_CARD: {**_CARD, "description": "Changed in transit"},
    Fault.CARD_FOR_ANOTHER_AGENT: signed_card(demo_manifest(OTHER_AGENT_URN)),
    Fault.CARD_WITHOUT_EXTENSION: _signed_without_extension(_CARD),
    Fault.CARD_INTERFACE_ELSEWHERE: signed_card(interface_url=f"{BASE_URL}/rpc"),
}


class FakeA2AGateway:
    """Handler for ``httpx.MockTransport``; it records each path and each bearer token.

    ``POST /compliance/probe-tokens`` mints a fresh set of JWS-shaped tokens; a
    token's kind is ``valid``, ``out_of_grant``, or ``wrong_audience``.
    """

    def __init__(self, *faults: Fault) -> None:
        self.faults = frozenset(faults)
        self.requests: list[str] = []
        self.received_tokens: list[str] = []
        self.issued: dict[str, str] = {}
        self._used: set[str] = set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if request.url.path == PROBE_TOKENS_PATH:
            return httpx.Response(200, json=self._probe_token_set())
        if request.url.path == "/a2a":
            return self._call(json.loads(request.content), request.headers)
        if request.url.path == "/.well-known/agent-card.json":
            return self._card()
        if request.url.path == "/.well-known/asap/manifest.json":
            return self._manifest()
        return httpx.Response(404, json={"detail": "Not Found"})

    def _manifest(self) -> httpx.Response:
        if Fault.NO_MANIFEST in self.faults:
            return httpx.Response(404, json={"detail": "Not Found"})
        if Fault.MANIFEST_ERROR in self.faults:
            return httpx.Response(500, json={"detail": "Internal Server Error"})
        version = "2.0.0" if Fault.MANIFEST_OF_ANOTHER_VERSION in self.faults else "1.0.0"
        return httpx.Response(200, json=demo_manifest(version=version).model_dump(mode="json"))

    def _card(self) -> httpx.Response:
        if Fault.NO_CARD in self.faults:
            return httpx.Response(404, json={"detail": "Not Found"})
        if Fault.CARD_NOT_JSON in self.faults:
            return httpx.Response(200, content=b"<html>card</html>")
        card = next((_CARDS_BY_FAULT[f] for f in self.faults if f in _CARDS_BY_FAULT), _CARD)
        return httpx.Response(200, json=card)

    def _call(self, call: dict[str, Any], headers: httpx.Headers) -> httpx.Response:
        if headers.get("a2a-version") != "1.0":
            return _rpc_error(call, -32009)
        if Fault.ANSWERS_200_TO_EVERYTHING in self.faults:
            return _greeting(call)
        authorization = headers.get("authorization")
        if authorization is None:
            if Fault.AUTH_REQUIRED_AS_RPC_ERROR in self.faults:
                return _rpc_error(call, -32001)
            return self._refusal(401, "invalid_token", "asap:auth_required", "Bearer")
        token = authorization.removeprefix("Bearer ")
        self.received_tokens.append(token)
        response = self._with_token(call, token)
        if Fault.ECHOES_TOKEN_IN_HEADER in self.faults:
            response.headers["x-received-token"] = token
        return response

    def _with_token(self, call: dict[str, Any], token: str) -> httpx.Response:
        kind = self._token_kind(token)
        if kind is not None:
            return self._grant_layer(call, kind)
        status = 403 if Fault.INVALID_TOKEN_AS_403 in self.faults else 401
        echo = f": got {token}" if Fault.ECHOES_TOKEN_IN_BODY in self.faults else ""
        code = f"asap:invalid_token{echo}"
        return self._refusal(status, "invalid_token", code, _INVALID_CHALLENGE)

    def _grant_layer(self, call: dict[str, Any], kind: str) -> httpx.Response:
        """Skill selection, then the grant, then its constraint."""
        message = call["params"]["message"]
        skill_id = message.get("metadata", {}).get(ASAP_EXTENSION_URI, {}).get("skill_id")
        if skill_id not in ("echo", "greet") and Fault.RUNS_UNDECLARED_SKILL not in self.faults:
            return _rpc_error(call, -32602)
        if kind == "out_of_grant" and Fault.GRANTS_EVERYTHING not in self.faults:
            no_challenge = Fault.DENIAL_WITHOUT_CHALLENGE in self.faults
            challenge = None if no_challenge else _SCOPE_CHALLENGE
            return self._refusal(403, "insufficient_scope", "asap:capability_denied", challenge)
        language = message["parts"][0]["data"].get("language")
        if language not in ("en", "pt") and Fault.IGNORES_CONSTRAINTS not in self.faults:
            with_challenge = Fault.CONSTRAINT_WITH_CHALLENGE in self.faults
            challenge = _SCOPE_CHALLENGE if with_challenge else None
            return self._refusal(403, "access_denied", "asap:constraint_violation", challenge)
        return _greeting(call)

    def _refusal(self, status: int, error: str, code: str, challenge: str | None) -> httpx.Response:
        if Fault.NO_WWW_AUTHENTICATE in self.faults:
            challenge = None
        headers = {} if challenge is None else {"www-authenticate": challenge}
        body = {"error": error, "error_description": code}
        return httpx.Response(status, json=body, headers=headers)

    def _token_kind(self, token: str) -> str | None:
        """The token layer: signature, audience, and (with a fault) single use."""
        kind = self.issued.get(token)
        if kind is None and Fault.ACCEPTS_BAD_SIGNATURE in self.faults:
            signed_part = token.rsplit(".", 1)[0]
            kind = next((k for t, k in self.issued.items() if t.startswith(signed_part)), None)
        if kind == "wrong_audience" and Fault.IGNORES_AUDIENCE not in self.faults:
            return None
        replayed = token in self._used and Fault.SINGLE_USE_TOKENS in self.faults
        self._used.add(token)
        return None if replayed or Fault.REFUSES_VALID_TOKENS in self.faults else kind

    def _probe_token_set(self) -> dict[str, Any]:
        def mint(kind: str) -> str:
            claims = {"jti": secrets.token_hex(8), "aud": f"{BASE_URL}/a2a", "kind": kind}
            parts = [{"alg": "ES256", "typ": "at+jwt"}, claims]
            signed = ".".join(_b64url(json.dumps(part).encode()) for part in parts)
            token = f"{signed}.{_b64url(secrets.token_bytes(64))}"
            self.issued[token] = kind
            return token

        kinds = ("valid", "out_of_grant", "wrong_audience")
        return {"test_only": True, "external": {kind: mint(kind) for kind in kinds}}


_INVALID_CHALLENGE = 'Bearer error="invalid_token", error_description="asap:invalid_token"'
_SCOPE_CHALLENGE = 'Bearer error="insufficient_scope", error_description="asap:capability_denied"'


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _rpc_error(call: dict[str, Any], code: int) -> httpx.Response:
    error = {"code": code, "message": "refused"}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": call.get("id"), "error": error})


def _greeting(call: dict[str, Any]) -> httpx.Response:
    message = {"role": "ROLE_AGENT", "messageId": "m-1", "parts": [{"data": {"greeting": "Hi"}}]}
    result = {"message": message}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": call.get("id"), "result": result})
