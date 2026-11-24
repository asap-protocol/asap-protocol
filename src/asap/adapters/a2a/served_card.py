"""The signed Agent Card of a served agent, from one config (v2.6.0).

:func:`build_signed_agent_card` composes the manifest mapping, the card
security of an external-token config (:func:`external_token_card_security`),
and the signatures. It returns a card only when every signer signed it:
a card that cannot be signed (for example a skill schema with an empty value,
which a signature would not cover) raises, so it is never served.

The default is one ``EdDSA`` signature. ``a2a-sdk`` verifiers that allow only
``ES256`` and ``RS256`` reject such a card; add an ``ES256`` signer for them.

Example:
    >>> mapping = build_signed_agent_card(manifest, inputs,
    ...     signers=[CardSigner(key=ed25519_key, kid="card-1")],
    ...     external_tokens=token_config)  # doctest: +SKIP
    >>> mapping.card.signatures[0].protected  # doctest: +SKIP
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from asap.adapters.a2a.errors import A2ACardNotSignableError, A2AMappingInputError
from asap.adapters.a2a.mapping import AgentCardInputs, CardMapping, manifest_to_agent_card
from asap.adapters.a2a.signing import CardSigningAlgorithm, CardSigningKey, sign_agent_card
from asap.adapters.a2a.token_security import external_token_card_security
from asap.auth.external_tokens import ExternalTokenConfig
from asap.models.entities import Manifest

_SECURITY_INPUTS = ("security_schemes", "security_requirements", "skill_security_requirements")


@dataclass(frozen=True, slots=True)
class CardSigner:
    """One signature to add to a served card: the private key, its ``kid``, and the algorithm.

    ``alg`` is ``EdDSA`` (an Ed25519 key, the default) or ``ES256`` (a P-256
    key). ``jku`` goes into the protected header when set.
    """

    key: CardSigningKey = field(repr=False)
    kid: str
    alg: CardSigningAlgorithm = "EdDSA"
    jku: str | None = None


def build_signed_agent_card(
    manifest: Manifest,
    inputs: AgentCardInputs,
    *,
    signers: Sequence[CardSigner],
    external_tokens: ExternalTokenConfig | None = None,
) -> CardMapping:
    """Map *manifest* to an A2A v1.0 Agent Card and sign it once per signer, in order.

    With *external_tokens*, the card security comes from that config only, and
    *inputs* must not set any security field.

    Raises:
        A2ACardNotSignableError: *signers* is empty, or a signer refuses the card
            (``paths`` lists the empty or inexact values, for example in a skill schema).
        A2AMappingInputError: *inputs* sets security and *external_tokens* is given,
            or an input breaks a mapping rule.
        A2AMappingError: The card would not be valid.
    """
    if not signers:
        raise A2ACardNotSignableError(
            "a served card needs at least one signer; got none, and an unsigned card "
            "is never served"
        )
    if external_tokens is not None:
        inputs = _with_external_token_security(inputs, manifest, external_tokens)
    mapping = manifest_to_agent_card(manifest, inputs)
    card = mapping.card
    for signer in signers:
        card = sign_agent_card(card, signer.key, alg=signer.alg, kid=signer.kid, jku=signer.jku)
    return CardMapping(card=card, dropped=mapping.dropped)


def _with_external_token_security(
    inputs: AgentCardInputs, manifest: Manifest, tokens: ExternalTokenConfig
) -> AgentCardInputs:
    caller_set = [name for name in _SECURITY_INPUTS if getattr(inputs, name)]
    if caller_set:
        raise A2AMappingInputError(
            caller_set[0],
            f"inputs set {caller_set}, but the card security comes from external_tokens; "
            "pass one or the other",
        )
    skill_ids = [skill.id for skill in manifest.capabilities.skills]
    security = external_token_card_security(tokens, skill_ids)
    return inputs.model_copy(
        update={
            "security_schemes": security.security_schemes,
            "security_requirements": security.security_requirements,
            "skill_security_requirements": security.skill_security_requirements,
        }
    )
