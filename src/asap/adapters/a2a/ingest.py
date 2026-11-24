"""Ingest an A2A v1.0 Agent Card: fetch, verify, then map it to an ASAP manifest.

The steps run in this order (COMPAT-005, COMPAT-006): fetch
the card through :func:`asap.utils.guarded_fetch.guarded_fetch_json`; parse it;
verify its signatures under the :class:`A2ATrustConfig`; map the parsed card
(:func:`asap.adapters.a2a.agent_card_to_manifest`). A card that fails a step
is never mapped and never cached, and every failure is an
:class:`asap.adapters.a2a.A2AAdapterError`.

Example:
    >>> ingestor = AgentCardIngestor(A2ATrustConfig(pinned_keys=keys))  # doctest: +SKIP
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
from asap.adapters.a2a.errors import A2ACardFetchError, A2AMappingError
from asap.adapters.a2a.mapping import agent_card_to_manifest
from asap.adapters.a2a.models import AgentCard
from asap.adapters.a2a.trust import A2AKeyResolver, A2ATrustConfig
from asap.adapters.a2a.unmapped import DroppedField
from asap.adapters.a2a.verification import CardKeyResolver, CardVerification, verify_agent_card
from asap.models.entities import Endpoint, Manifest
from asap.utils.guarded_fetch import AddressResolver, GuardedFetchError, guarded_fetch_json


@dataclass(frozen=True, slots=True)
class CardIngestResult:
    """Result of :meth:`AgentCardIngestor.ingest`.

    ``verified`` is false only for an unsigned card accepted under
    ``allow_unsigned``: then the whole manifest is unverified data.
    ``dropped`` lists the card fields the manifest does not carry;
    ``uncovered`` lists the values of a verified card that no signature covers.
    """

    manifest: Manifest
    verified: bool
    signer_kids: tuple[str, ...]
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
        ASAP extension needs neither.

        Raises:
            A2ACardFetchError: The guarded fetch refused or failed.
            A2AUnsupportedVersionError: The document is a v0.3 card.
            A2ACardVerificationError: The signatures fail the trust config.
            A2AMappingError, A2AMappingInputError: The card does not parse or map.
        """
        cached = self._cards.get(url)
        verification = cached or await self._fetch_and_verify(url)
        mapping = agent_card_to_manifest(verification.card, agent_id=agent_id, endpoints=endpoints)
        # Cached only once verified and mapped, and never re-put on a hit, so a
        # card is fetched again at least once per cache lifetime.
        if cached is None:
            self._cards.put(url, verification)
        return CardIngestResult(
            manifest=mapping.manifest,
            verified=verification.verified,
            signer_kids=verification.signer_kids,
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
            raise A2ACardFetchError(url, exc.code) from exc
        card = _parse_card(url, document)
        return await verify_agent_card(
            card, self._key_resolver, algorithms=self._trust.algorithms, policy=self._trust.policy
        )


def _parse_card(url: str, document: dict[str, Any]) -> AgentCard:
    try:
        return AgentCard.model_validate(document)
    except ValidationError as exc:
        source = f"Agent Card at {url!r}"
        raise A2AMappingError.from_validation_error(source, "A2A v1.0 Agent Card", exc) from exc
