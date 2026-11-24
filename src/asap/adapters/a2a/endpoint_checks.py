"""Checks on the ASAP endpoints that an Agent Card names.

A manifest's ``endpoints.asap`` is a URL that some later code will call from
its own network, so a card that names it must not point that call at a cloud
metadata address, a loopback admin page, or a local file.

- :func:`require_card_endpoint_shape` (pure, used by the mapping): ``https``
  for the ASAP endpoint and ``wss`` for the events endpoint, a host, and no
  user info.
- :func:`require_public_endpoint_hosts` (used by ingest): the guarded-fetch
  URL check, so no IP literal that is not public and no ``localhost`` name,
  unless ``allow_private_hosts``.

Neither check resolves DNS: a hostname that later resolves to a private
address is the concern of whatever fetches the endpoint, through its own
guarded fetch. Mapping cannot know what a name will resolve to then.
"""

from __future__ import annotations

from typing import Literal

import httpx

from asap.adapters.a2a.errors import A2AMappingError, describe_url
from asap.models.entities import Endpoint
from asap.utils.guarded_fetch import GuardedFetchError, validate_guarded_url

EndpointField = Literal["asap", "events"]

_SCHEME_FOR_FIELD: dict[EndpointField, str] = {"asap": "https", "events": "wss"}


def require_card_endpoint_shape(field: EndpointField, url: str) -> None:
    """Raise unless *url* is an ``https`` (``wss`` for events) URL with a host and no user info.

    Raises:
        A2AMappingError: ``fields`` is ``("endpoints.<field>",)``.

    Example:
        >>> require_card_endpoint_shape("asap", "https://agent.example.com/asap")
    """
    scheme = _SCHEME_FOR_FIELD[field]
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        parsed = None
    if parsed is None or parsed.scheme != scheme or not parsed.host or parsed.userinfo:
        raise A2AMappingError(
            f"the card's {field} endpoint {describe_url(url)} must be a {scheme} URL with a "
            "host and no user info",
            fields=[f"endpoints.{field}"],
        )


def require_public_endpoint_hosts(endpoints: Endpoint, *, allow_private_hosts: bool) -> None:
    """Raise if an endpoint's host is an IP literal that is not public, or ``localhost``.

    The ``wss`` events endpoint is checked as ``https``: only the host matters here.

    Raises:
        A2AMappingError: ``fields`` is ``("endpoints.<field>",)``.
    """
    named: tuple[tuple[EndpointField, str | None], ...] = (
        ("asap", endpoints.asap),
        ("events", endpoints.events),
    )
    for field, url in named:
        if url is None:
            continue
        try:
            as_https = str(httpx.URL(url).copy_with(scheme="https"))
            validate_guarded_url(as_https, allow_private_hosts=allow_private_hosts)
        except (httpx.InvalidURL, GuardedFetchError) as exc:
            refusal = exc.code if isinstance(exc, GuardedFetchError) else "invalid_url"
            raise A2AMappingError(
                f"the card's {field} endpoint {describe_url(url)} is refused ({refusal}); "
                "allow_private_hosts in the trust config allows a private host, for "
                "development only",
                fields=[f"endpoints.{field}"],
            ) from None
