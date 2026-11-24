"""``TokenIntrospector`` seams for the external-token path.

``poster`` routes the RFC 7662 request through a caller's function (the
guarded POST) instead of the built-in httpx client; ``TokenInfo.claims``
keeps every member of the response. Without a poster nothing changes.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from asap.auth.introspection import TokenIntrospector

_URL = "https://idp.example/oauth/introspect"


class _FakePoster:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str], tuple[str, str]]] = []

    async def __call__(
        self, url: str, form: Mapping[str, str], *, basic_auth: tuple[str, str]
    ) -> dict[str, Any]:
        self.calls.append((url, dict(form), basic_auth))
        return self.response


async def test_poster_carries_the_request_and_claims_keep_the_response() -> None:
    response = {
        "active": True,
        "sub": "user-1",
        "aud": ["https://api.example/asap"],
        "exp": int(time.time()) + 600,
        "groups": ["ops"],
    }
    poster = _FakePoster(response)
    introspector = TokenIntrospector(_URL, "rs", "s3cret", poster=poster)

    info = await introspector.introspect("opaque-token")

    assert info is not None and info.sub == "user-1"
    assert info.claims == response
    assert poster.calls == [
        (_URL, {"token": "opaque-token", "token_type_hint": "access_token"}, ("rs", "s3cret"))
    ]


async def test_inactive_answer_through_the_poster_is_none() -> None:
    introspector = TokenIntrospector(_URL, "rs", "s3cret", poster=_FakePoster({"active": False}))
    assert await introspector.introspect("opaque-token") is None
