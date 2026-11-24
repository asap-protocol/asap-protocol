"""Client side of the A2A flow: an ``a2a-sdk`` client that trusts one pinned card key.

The card verifier never follows a ``jku``: a pinned ``kid`` is the only key
it trusts, and a signature under any other ``kid`` is skipped, so a card
signed only by an unknown key fails with ``InvalidSignaturesError``. The
access token goes out through the SDK's ``AuthInterceptor``, which reads the
card's ``securityRequirements``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import httpx
import jwt
from a2a.client import ClientCallContext, ClientCallInterceptor, ClientConfig, create_client
from a2a.client.auth import AuthInterceptor, CredentialService
from a2a.helpers import new_data_part
from a2a.types import AgentCard, Message, Role, SendMessageRequest
from a2a.utils.signing import create_signature_verifier
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from google.protobuf.json_format import MessageToDict

from examples.a2a_gateway.skill_call import SKILL_SELECTION_KEY


def pinned_card_verifier(kid: str, public_key: Ed25519PublicKey) -> Callable[[AgentCard], None]:
    """Return an ``a2a-sdk`` card verifier that accepts only an ``EdDSA`` signature by *kid*.

    Example:
        >>> verify = pinned_card_verifier("gateway-card-1", card_public_key)  # doctest: +SKIP
        >>> await A2ACardResolver(http, origin).get_agent_card(signature_verifier=verify)
    """
    pem = public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)

    def key_provider(card_kid: str | None, jku: str | None) -> bytes:
        # The jku is ignored on purpose: following a card's own key URL would let
        # the card choose the key that verifies it.
        if card_kid != kid:
            raise jwt.InvalidKeyError("the signature's kid is not pinned")
        return pem

    return create_signature_verifier(key_provider, ["EdDSA"])


class BearerCredentials(CredentialService):
    """Hand one access token to ``AuthInterceptor`` for the schemes the card names."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def get_credentials(
        self, security_scheme_name: str, context: ClientCallContext | None
    ) -> str | None:
        """Return the token, whatever the scheme name (the demo has one issuer)."""
        return self._token


def skill_message(skill_id: str, arguments: dict[str, Any]) -> Message:
    """A user message that selects *skill_id* and carries *arguments* as one data part."""
    message = Message(
        role=Role.ROLE_USER, message_id=str(uuid4()), parts=[new_data_part(arguments)]
    )
    message.metadata.update({SKILL_SELECTION_KEY: {"skill_id": skill_id}})
    return message


async def call_skill(
    http: httpx.AsyncClient,
    origin: str,
    *,
    verify_card: Callable[[AgentCard], None],
    token: str | None,
    skill_id: str,
    arguments: dict[str, Any],
) -> Any:
    """Resolve and verify the card at *origin*, call *skill_id*, and return the reply data.

    Raises:
        a2a.utils.signing.SignatureVerificationError: The card does not verify.
        a2a.client.errors.A2AClientError: The gateway refused the call; its
            ``__cause__`` is the ``httpx.HTTPStatusError`` with the HTTP answer.
    """
    interceptors: list[ClientCallInterceptor] = (
        [AuthInterceptor(BearerCredentials(token))] if token else []
    )
    config = ClientConfig(httpx_client=http, streaming=False)
    client = await create_client(
        origin, client_config=config, interceptors=interceptors, signature_verifier=verify_card
    )
    try:
        request = SendMessageRequest(message=skill_message(skill_id, arguments))
        replies = [event async for event in client.send_message(request)]
    finally:
        await client.close()
    return MessageToDict(replies[0].message.parts[0].data)
