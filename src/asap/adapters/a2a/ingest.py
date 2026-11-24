"""Ingest an A2A v1.0 Agent Card: fetch, verify, then map it to an ASAP manifest.

The steps run in this order (COMPAT-005, COMPAT-006): fetch
the card through :func:`asap.utils.guarded_fetch.guarded_fetch_json`; parse it;
verify its signatures under the :class:`A2ATrustConfig` (which also refuses a
signed card whose ASAP extension params hold values that no signature covers);
map the parsed card (:func:`asap.adapters.a2a.agent_card_to_manifest`); refuse
endpoints on a private host; require that a signer that verified may sign for
the manifest's agent (:meth:`CardVerification.require_signer_for`). A card that
fails a step is never cached. A failure of the card, its
fetch, or its keys is an :class:`asap.adapters.a2a.A2AAdapterError`; an
exception raised by an injected ``key_resolver`` or ``transport`` propagates
as it is.

Example:
    >>> ingestor = AgentCardIngestor(A2ATrustConfig(pinned_keys={
    ...     "research-card-1": PinnedCardKey(jwk=research_public_jwk,
    ...                                      agents=("urn:asap:agent:research",))}))  # doctest: +SKIP
    >>> result = await ingestor.ingest("https://agent.example.com/.well-known/agent-card.json")
    >>> result.manifest.id, result.verified  # doctest: +SKIP
    ('urn:asap:agent:research', True)
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from asap.adapters.a2a._ttl_cache import BoundedTTLCache
from asap.adapters.a2a.canonical import canonical_card_form
from asap.adapters.a2a.endpoint_checks import require_public_endpoint_hosts
from asap.adapters.a2a.errors import (
    A2ACardFetchError,
    A2AMappingError,
    describe_url,
)
from asap.adapters.a2a.mapping import agent_card_to_manifest
from asap.adapters.a2a.models import AgentCard
from asap.adapters.a2a.trust import A2AKeyResolver, A2ATrustConfig
from asap.adapters.a2a.unmapped import DroppedField
from asap.adapters.a2a.verification import (
    CardKeyResolver,
    CardVerification,
    VerifiedCardSigner,
    verify_agent_card,
)
from asap.models.entities import Endpoint, Manifest
from asap.utils.guarded_fetch import AddressResolver, GuardedFetchError, guarded_fetch_json


@dataclass(frozen=True, slots=True)
class CardIngestResult:
    """Result of :meth:`AgentCardIngestor.ingest`.

    ``verified`` true means: a signature over the card verified with a trusted
    key, no present signature failed, the ASAP extension params are covered,
    and a signer that verified may sign for ``manifest.id``. It does not bind
    the card to the URL it came from, it says nothing about freshness (a
    signed card can be replayed from any URL while its key is trusted), and
    the card is only as strong as the weakest trusted key that may sign for
    that agent; see :mod:`asap.adapters.a2a.verification`. ``verified`` is
    false only for an unsigned card accepted under ``allow_unsigned``: the
    mark is on this result, not on the :class:`Manifest`, and then the whole
    manifest is unverified data. ``signers`` lists each signer that verified
    (source, ``kid``, scope). ``dropped`` lists the card fields the manifest
    does not carry; ``uncovered`` lists the values of a verified card that no
    signature covers, none of them in the manifest.
    """

    manifest: Manifest
    verified: bool
    signers: tuple[VerifiedCardSigner, ...]
    dropped: tuple[DroppedField, ...]
    uncovered: tuple[str, ...]


class AgentCardIngestor:
    """Fetch, verify, and map Agent Cards, caching each verified card by URL.

    A cached card is reused for ``cache_ttl_seconds`` and the cache holds at
    most ``cache_max_entries`` cards. ``key_resolver`` defaults to an
    :class:`A2AKeyResolver` over *trust*. ``dns_resolver``, ``transport``, and
    ``clock`` are for tests.
    """

    def __init__(
        self,
        trust: A2ATrustConfig,
        *,
        key_resolver: CardKeyResolver | None = None,
        cache_ttl_seconds: float = 300.0,
        cache_max_entries: int = 256,
        dns_resolver: AddressResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._trust = trust
        self._key_resolver = key_resolver or A2AKeyResolver(
            trust, dns_resolver=dns_resolver, transport=transport, clock=clock
        )
        self._cards: BoundedTTLCache[CardVerification] = BoundedTTLCache(
            ttl_seconds=cache_ttl_seconds, max_entries=cache_max_entries, clock=clock
        )
        self._dns_resolver = dns_resolver
        self._transport = transport

    async def ingest(
        self, url: str, *, agent_id: str | None = None, endpoints: Endpoint | None = None
    ) -> CardIngestResult:
        """Return the manifest of the card at *url*, with its verification status.

        *agent_id* and *endpoints* are passed to the mapping; a card with the
        ASAP extension needs neither. When *endpoints* is not given, the
        manifest's endpoints come from the card, and a host that is an IP
        literal that is not public, or ``localhost``, is refused unless the
        trust config has ``allow_private_hosts``. A hostname that resolves to a
        private address later is the concern of whatever fetches the endpoint.

        Raises:
            A2ACardFetchError: The guarded fetch refused or failed.
            A2AUnsupportedVersionError: The document is a v0.3 card.
            A2ACardVerificationError: The signatures fail the trust config.
            A2ASignerScopeError: No signer that verified may sign for the
                manifest's agent (the card's URN, or *agent_id*).
            A2AMappingError, A2AMappingInputError: The card does not parse or map,
                or names an endpoint that is refused.
        """
        cached = self._cards.get(url)
        verification = cached or await self._fetch_and_verify(url)
        mapping = agent_card_to_manifest(verification.card, agent_id=agent_id, endpoints=endpoints)
        if endpoints is None:
            require_public_endpoint_hosts(
                mapping.manifest.endpoints, allow_private_hosts=self._trust.allow_private_hosts
            )
        verification.require_signer_for(mapping.manifest.id)
        # Cached only once verified and mapped, and never re-put on a hit, so a
        # card is fetched again at least once per cache lifetime.
        if cached is None:
            self._cards.put(url, verification)
        return CardIngestResult(
            manifest=mapping.manifest,
            verified=verification.verified,
            signers=verification.signers,
            dropped=mapping.dropped,
            uncovered=verification.uncovered,
        )

    async def _fetch_and_verify(self, url: str) -> CardVerification:
        try:
            document = await guarded_fetch_json(
                url,
                allow_private_hosts=self._trust.allow_private_hosts,
                resolver=self._dns_resolver,
                transport=self._transport,
            )
        except GuardedFetchError as exc:
            error = A2ACardFetchError(exc.url, exc.code)
            # The guarded fetch repeats a URL that does not parse as given, user
            # info included: then its error must not be chained into a traceback.
            raise error from (exc if error.url == exc.url else None)
        card = _parse_card(url, document)
        verification = await verify_agent_card(
            card, self._key_resolver, algorithms=self._trust.algorithms, policy=self._trust.policy
        )
        if not verification.verified:
            # Verification skips the canonical form of an unsigned card. Build it
            # anyway: a card that has none (nested too deep) must fail typed here,
            # not inside the mapping.
            canonical_card_form(card)
        return verification


def _parse_card(url: str, document: dict[str, Any]) -> AgentCard:
    try:
        return AgentCard.model_validate(document)
    except ValidationError as exc:
        source = f"Agent Card at {describe_url(url)}"
        raise A2AMappingError.from_validation_error(source, "A2A v1.0 Agent Card", exc) from exc
