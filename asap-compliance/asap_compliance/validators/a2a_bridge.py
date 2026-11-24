"""A2A bridge compliance validation (profile ``a2a-bridge``).

Runs against a remote A2A v1.0 agent served by an ASAP gateway, over plain HTTP
(:mod:`asap_compliance.validators.a2a_transport`). The card checks fetch the
card once and judge it under the trust of the profile config only: the
expected agent URN and the card keys, never a key or a URN from the target. A
verified card proves who signed it, not where it was served from or that it is
fresh, and no check claims either. ``_PROFILE_CHECKS`` lists the check IDs;
each check picks the credential of its case.

Example::

    config = A2ABridgeComplianceConfig(
        base_url="http://127.0.0.1:8788",
        allow_private_targets=True,  # local and CI runs only
        expected_agent_urn="urn:asap:agent:a2a-gateway-demo",
        card_keys={"gateway-card-1": card_public_jwk},
        probe_tokens_url="http://127.0.0.1:8788/compliance/probe-tokens",
    )
    result = await validate_a2a_bridge_async(config)
    assert result.passed, [check for check in result.checks if not check.passed]
"""

from __future__ import annotations

import asyncio
import base64
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import ValidationError

from asap.adapters.a2a import (
    ASAP_EXTENSION_URI,
    A2AAdapterError,
    A2AInvalidSignatureError,
    A2AKeyResolver,
    A2AMappingError,
    A2ANoTrustedSignatureError,
    A2ASignerScopeError,
    A2AUnsignedCardError,
    A2AUnsupportedVersionError,
    AgentCard,
    read_asap_extension,
    verify_agent_card_for_agent,
)

from asap_compliance.config import A2ABridgeComplianceConfig
from asap_compliance.validators.a2a_answers import (
    CONSTRAINT_VIOLATION_403,
    INVALID_PARAMS,
    message_success_problem,
    rpc_error_problem,
)
from asap_compliance.validators.a2a_transport import (
    A2A_PROTOCOL_VERSION,
    AGENT_CARD_PATH,
    ASAP_MANIFEST_PATH,
    A2AHttpResponse,
    A2AHttpTransport,
)
from asap_compliance.validators.handshake import CheckResult
from asap_compliance.validators.http_answers import (
    AUTH_REQUIRED_401,
    CAPABILITY_DENIED_403,
    INVALID_TOKEN_401,
)
from asap_compliance.validators.http_exchange import HttpExchangeError
from asap_compliance.validators.probe_tokens import ProbeTokenKind, ProbeTokenProvider
from asap_compliance.validators.profile_run import (
    CheckAbortedError,
    ProfileCheck,
    echoed_token_problem,
    endpoint_token_provider,
    run_profile_checks,
    unchallenged_401_problem,
)

# Built by the harness; no gateway accepts it.
_MALFORMED_TOKEN = "asap-compliance-malformed-token"
_UNDECLARED_SKILL = "asap_compliance_undeclared_skill"
_EXPECT_MESSAGE = "HTTP 200 and a JSON-RPC result whose message has a data part"
_EXPECT_AUTH_REQUIRED = "HTTP 401, a Bearer challenge with no error, asap:auth_required"
_EXPECT_INVALID_TOKEN = (
    'HTTP 401, a Bearer challenge with error="invalid_token", asap:invalid_token'
)
_EXPECT_CAPABILITY_DENIED = (
    'HTTP 403, a Bearer challenge with error="insufficient_scope", asap:capability_denied'
)
_EXPECT_CONSTRAINT_VIOLATION = (
    "HTTP 403 with no WWW-Authenticate header, access_denied, asap:constraint_violation"
)
_EXPECT_INVALID_PARAMS = f"HTTP 200 and a JSON-RPC error {INVALID_PARAMS} for the request"
_EXPECT_CARD_DOCUMENT = f"HTTP 200 and a JSON object at {AGENT_CARD_PATH}"
_EXPECT_CARD_V1 = "a card that parses as an A2A v1.0 Agent Card"
_EXPECT_CARD_SIGNATURE = (
    "a signature by a configured key, none that fails, and a signer that may sign "
    "for the agent that the card names"
)
_EXPECT_CARD_AGENT_URN = "an ASAP extension in the card that names the expected agent URN"
_EXPECT_INTERFACE = f"a JSONRPC interface with protocol version {A2A_PROTOCOL_VERSION} in the card"
_EXPECT_SAME_AGENT = (
    "the URN, name, and version of the card in the ASAP manifest on the same origin, "
    "when there is one (COMPAT-003)"
)
_VERIFICATION_FAILURES: dict[type[A2AAdapterError], str] = {
    A2AUnsignedCardError: "the card has no signature",
    A2ANoTrustedSignatureError: (
        "no signature on the card is by a configured key with an allowed algorithm"
    ),
}


@dataclass
class A2ABridgeResult:
    """Summary for the ``a2a-bridge`` compliance profile: one entry per check ID."""

    profile: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass
class _BridgeRun:
    """One profile run: the target, the token source, and every token and answer so far."""

    config: A2ABridgeComplianceConfig
    transport: A2AHttpTransport
    tokens: ProbeTokenProvider
    sent_tokens: list[str] = field(default_factory=list)
    responses: list[A2AHttpResponse] = field(default_factory=list)
    _card_answer: A2AHttpResponse | HttpExchangeError | None = None

    async def card_answer(self) -> A2AHttpResponse:
        """The answer at the card's well-known path, fetched once per run, failure included."""
        if self._card_answer is None:
            try:
                self._card_answer = await self.transport.fetch_card()
                self.responses.append(self._card_answer)
            except HttpExchangeError as exc:
                self._card_answer = exc
        if isinstance(self._card_answer, HttpExchangeError):
            raise self._card_answer
        return self._card_answer

    async def card(self) -> AgentCard:
        """The card, parsed as A2A v1.0; raise :class:`CheckAbortedError` if there is none."""
        answer = await self.card_answer()
        problem = _card_document_problem(answer)
        if problem is not None:
            raise CheckAbortedError(f"no card to check: {problem}")
        try:
            return AgentCard.model_validate(answer.json_body)
        except A2AUnsupportedVersionError:
            raise CheckAbortedError("the card is an A2A v0.3 card, not A2A v1.0") from None
        except ValidationError as exc:
            # The count only: a validation message can repeat card text.
            raise CheckAbortedError(
                f"the card does not parse as A2A v1.0 ({exc.error_count()} problems)"
            ) from None

    async def call(
        self, skill_id: str, arguments: Mapping[str, Any], token: str | None
    ) -> A2AHttpResponse:
        """One ``SendMessage``; the token and the answer join the run's records."""
        if token is not None:
            self.sent_tokens.append(token)
        response = await self.transport.send_message(skill_id, arguments, bearer_token=token)
        self.responses.append(response)
        return response

    async def call_skill(self, token: str | None) -> A2AHttpResponse:
        """Call the configured skill with the arguments that its grant allows."""
        return await self.call(self.config.skill_id, self.config.allowed_arguments, token)

    async def manifest_answer(self) -> A2AHttpResponse:
        """The answer at the ASAP manifest's well-known path."""
        response = await self.transport.fetch_manifest()
        self.responses.append(response)
        return response


def _card_document_problem(response: A2AHttpResponse) -> str | None:
    if response.status_code != 200:
        return f"GET {AGENT_CARD_PATH} answered HTTP {response.status_code}"
    if response.json_body is None:
        return f"GET {AGENT_CARD_PATH} answered with a body that is not a JSON object"
    return None


async def _check_card_well_known(run: _BridgeRun) -> str | None:
    """The card at the well-known path, with no token."""
    return _card_document_problem(await run.card_answer())


async def _check_card_a2a_v1(run: _BridgeRun) -> str | None:
    """That card, parsed with ``asap.adapters.a2a.AgentCard``."""
    await run.card()
    return None


async def _check_card_signature(run: _BridgeRun) -> str | None:
    """The card under the configured keys, each pinned for the expected URN only (COMPAT-006)."""
    card = await run.card()
    expected = run.config.expected_agent_urn
    resolver = A2AKeyResolver(run.config.card_trust())
    try:
        await verify_agent_card_for_agent(card, _named_agent_urn(card) or expected, resolver)
    except A2ASignerScopeError:
        return (
            "a configured key verified the card, but the card names another agent, "
            f"and that key may sign for {expected} only"
        )
    except A2AInvalidSignatureError as exc:
        return f"signature {exc.index} fails the card ({exc.reason})"
    except A2AAdapterError as exc:
        return _VERIFICATION_FAILURES.get(
            type(exc), f"the card does not verify ({type(exc).__name__})"
        )
    return None


async def _check_card_agent_urn(run: _BridgeRun) -> str | None:
    """The card's ASAP extension against the expected URN, which only the config gives."""
    card = await run.card()
    expected = run.config.expected_agent_urn
    try:
        extension = read_asap_extension(card)
    except A2AMappingError:
        return "the card lists the ASAP extension more than once, or its params are not valid"
    if extension is None:
        return f"the card has no ASAP extension ({ASAP_EXTENSION_URI})"
    if extension.agent_urn != expected:
        return f"the card's ASAP extension names an agent URN other than {expected}"
    return None


async def _check_card_jsonrpc_interface(run: _BridgeRun) -> str | None:
    """The card's interfaces against the URL that the call checks use (never one from the card)."""
    card = await run.card()
    url = run.transport.a2a_url
    for interface in card.supported_interfaces:
        binding = (interface.protocol_binding, interface.protocol_version)
        if interface.url == url and binding == ("JSONRPC", A2A_PROTOCOL_VERSION):
            return None
    return f"the card lists no such interface at {url}"


async def _check_card_manifest_same_agent(run: _BridgeRun) -> str | None:
    """The card against the ASAP manifest on the same origin, if any (COMPAT-003)."""
    card = await run.card()
    response = await run.manifest_answer()
    manifest = response.json_body
    if response.status_code == 404:
        return None
    if response.status_code != 200 or manifest is None:
        return f"GET {ASAP_MANIFEST_PATH} answered HTTP {response.status_code}, not 200 or 404"
    pairs = {
        "URN": (_named_agent_urn(card), manifest.get("id")),
        "name": (card.name, manifest.get("name")),
        "version": (card.version, manifest.get("version")),
    }
    differ = [what for what, (on_card, in_manifest) in pairs.items() if on_card != in_manifest]
    return f"the manifest and the card differ in {', '.join(differ)}" if differ else None


async def _check_external_valid_token(run: _BridgeRun) -> str | None:
    """One fresh access token with the grant, sent twice: an access token is not single-use."""
    token = await run.tokens.probe_token(ProbeTokenKind.EXTERNAL)
    for use in ("first", "second"):
        problem = message_success_problem(await run.call_skill(token))
        if problem is not None:
            return f"on the {use} use, {problem}"
    return None


async def _check_auth_required(run: _BridgeRun) -> str | None:
    """No token."""
    return AUTH_REQUIRED_401.problem(await run.call_skill(None))


async def _check_invalid_token(run: _BridgeRun) -> str | None:
    """A malformed token (not a JWT)."""
    return INVALID_TOKEN_401.problem(await run.call_skill(_MALFORMED_TOKEN))


async def _check_bad_signature(run: _BridgeRun) -> str | None:
    """The header and claims of a fresh access token, with a random signature.

    The forged token names the configured issuer, so it reaches the signature check;
    the token it came from is never sent.
    """
    parts = (await run.tokens.probe_token(ProbeTokenKind.EXTERNAL)).split(".")
    if len(parts) != 3:
        return "the provider's access token is not a compact JWS, so it has no signature to forge"
    signature = secrets.token_bytes(max(len(parts[2]) * 3 // 4, 32))
    forged = ".".join([*parts[:2], base64.urlsafe_b64encode(signature).rstrip(b"=").decode()])
    return INVALID_TOKEN_401.problem(await run.call_skill(forged))


async def _check_external_wrong_audience(run: _BridgeRun) -> str | None:
    """A fresh access token from the same issuer, with the grant, for another audience."""
    token = await run.tokens.probe_token(ProbeTokenKind.EXTERNAL_WRONG_AUDIENCE)
    return INVALID_TOKEN_401.problem(await run.call_skill(token))


async def _check_external_wrong_capability(run: _BridgeRun) -> str | None:
    """A fresh access token whose scopes give no grant for the skill."""
    token = await run.tokens.probe_token(ProbeTokenKind.EXTERNAL_OUT_OF_GRANT)
    return CAPABILITY_DENIED_403.problem(await run.call_skill(token))


async def _check_external_constraint_violation(run: _BridgeRun) -> str | None:
    """A fresh access token with the grant, arguments that break the grant's constraint."""
    token = await run.tokens.probe_token(ProbeTokenKind.EXTERNAL)
    config = run.config
    response = await run.call(config.skill_id, config.constraint_violation_arguments, token)
    return CONSTRAINT_VIOLATION_403.problem(response)


async def _check_undeclared_skill(run: _BridgeRun) -> str | None:
    """A fresh access token with the grant, a skill id that the card does not list."""
    token = await run.tokens.probe_token(ProbeTokenKind.EXTERNAL)
    response = await run.call(_UNDECLARED_SKILL, run.config.allowed_arguments, token)
    return rpc_error_problem(response, INVALID_PARAMS)


async def _check_www_authenticate_on_401(run: _BridgeRun) -> str | None:
    """Every HTTP 401 answer of the run so far."""
    return unchallenged_401_problem(run.responses)


async def _check_token_not_echoed(run: _BridgeRun) -> str | None:
    """Every answer of the run so far, the card and manifest included, against every token."""
    return echoed_token_problem(run.sent_tokens, run.responses)


def _named_agent_urn(card: AgentCard) -> str | None:
    """The URN in the card's ASAP extension; None when it has none, or the extension is bad."""
    try:
        extension = read_asap_extension(card)
    except A2AMappingError:
        return None
    return None if extension is None else extension.agent_urn


# The checks, in the order they run.
_PROFILE_CHECKS: tuple[ProfileCheck[_BridgeRun], ...] = (
    ("card_well_known", _check_card_well_known, _EXPECT_CARD_DOCUMENT),
    ("card_a2a_v1", _check_card_a2a_v1, _EXPECT_CARD_V1),
    ("card_signature", _check_card_signature, _EXPECT_CARD_SIGNATURE),
    ("card_agent_urn", _check_card_agent_urn, _EXPECT_CARD_AGENT_URN),
    ("card_jsonrpc_interface", _check_card_jsonrpc_interface, _EXPECT_INTERFACE),
    ("card_manifest_same_agent", _check_card_manifest_same_agent, _EXPECT_SAME_AGENT),
    ("external_valid_token", _check_external_valid_token, f"{_EXPECT_MESSAGE}, twice"),
    ("auth_required", _check_auth_required, _EXPECT_AUTH_REQUIRED),
    ("invalid_token", _check_invalid_token, _EXPECT_INVALID_TOKEN),
    ("bad_signature", _check_bad_signature, _EXPECT_INVALID_TOKEN),
    ("external_wrong_audience", _check_external_wrong_audience, _EXPECT_INVALID_TOKEN),
    ("external_wrong_capability", _check_external_wrong_capability, _EXPECT_CAPABILITY_DENIED),
    (
        "external_constraint_violation",
        _check_external_constraint_violation,
        _EXPECT_CONSTRAINT_VIOLATION,
    ),
    ("undeclared_skill", _check_undeclared_skill, _EXPECT_INVALID_PARAMS),
    # These two judge every answer of the run, so they run last.
    (
        "www_authenticate_on_401",
        _check_www_authenticate_on_401,
        "a WWW-Authenticate header with the Bearer scheme on every HTTP 401",
    ),
    ("token_not_echoed", _check_token_not_echoed, "no answer that holds a token the run sent"),
)

A2A_BRIDGE_CHECK_IDS: tuple[str, ...] = tuple(check_id for check_id, _, _ in _PROFILE_CHECKS)


async def validate_a2a_bridge_async(
    config: A2ABridgeComplianceConfig,
    *,
    token_provider: ProbeTokenProvider | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> A2ABridgeResult:
    """Run the ``a2a-bridge`` profile against a remote A2A gateway.

    Args:
        config: Profile configuration: the origin, the card trust, and the skill to call.
        token_provider: Source of fresh probe tokens, asked once per request that needs
            one. Default: the gateway's test-only probe endpoint at ``probe_tokens_url``.
        http_transport: Optional httpx transport, for tests with a mock server.

    Returns:
        One ``CheckResult`` per ID in ``A2A_BRIDGE_CHECK_IDS``, in that order. A check
        that gets no full HTTP answer, or no token, fails, and the run goes on.

    Raises:
        ValueError: if there is neither a ``token_provider`` nor a ``probe_tokens_url``.
    """
    if token_provider is None:
        token_provider = endpoint_token_provider(
            config.profile,
            config.probe_tokens_url,
            timeout_seconds=config.timeout_seconds,
            http_transport=http_transport,
        )
    transport = A2AHttpTransport(
        config.base_url, timeout_seconds=config.timeout_seconds, http_transport=http_transport
    )
    run = _BridgeRun(config, transport, token_provider)
    checks = await run_profile_checks(run, _PROFILE_CHECKS, skip_checks=config.skip_checks)
    return A2ABridgeResult(profile=config.profile, checks=checks)


def validate_a2a_bridge(
    config: A2ABridgeComplianceConfig,
    *,
    token_provider: ProbeTokenProvider | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> A2ABridgeResult:
    """Sync wrapper for :func:`validate_a2a_bridge_async`."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        profile_run = validate_a2a_bridge_async(
            config, token_provider=token_provider, http_transport=http_transport
        )
        return asyncio.run(profile_run)
    raise RuntimeError(
        "Cannot call sync validate_a2a_bridge from inside a running event loop. "
        "Use validate_a2a_bridge_async."
    )
