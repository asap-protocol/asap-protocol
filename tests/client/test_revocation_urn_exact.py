"""The revoked-agent list matches a URN with equality.

``urn:asap:agent:revoked-01`` must not revoke a prefix, a case variant, or a
padded copy. ``is_revoked`` compares ``e.get("urn")`` with ``==``.
"""

from __future__ import annotations

import httpx
import pytest

from asap.client.revocation import is_revoked

_EXACT = "urn:asap:agent:revoked-01"
_NEIGHBORS: tuple[str, ...] = (
    "urn:asap:agent:revoked-010",
    "urn:asap:agent:revoked-0",
    "URN:asap:agent:revoked-01",
    "urn:asap:agent:revoked-01 ",
    " urn:asap:agent:revoked-01",
)
_REVOKED_URL = "https://example.com/revoked.json"


def _client() -> httpx.AsyncClient:
    """Client whose revoked list contains only ``_EXACT``."""

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == _REVOKED_URL
        return httpx.Response(
            200,
            json={
                "revoked": [
                    {
                        "urn": _EXACT,
                        "reason": "compromised",
                        "revoked_at": "2026-06-01T00:00:00Z",
                    }
                ],
                "version": "1.0",
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


@pytest.mark.asyncio
async def test_revoked_urn_match_is_exact() -> None:
    """The listed URN is revoked. A lookalike URN is not."""
    async with _client() as client:
        assert await is_revoked(_EXACT, revoked_url=_REVOKED_URL, http_client=client) is True
        for neighbor in _NEIGHBORS:
            revoked = await is_revoked(neighbor, revoked_url=_REVOKED_URL, http_client=client)
            assert revoked is False
