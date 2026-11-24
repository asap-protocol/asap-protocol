"""Guarded form POST: RFC 7662 introspection through the same guard.

Same guarantees as the GET: HTTPS only, public addresses with the connection
pinned, no redirects, a byte limit, and a deadline. The client credentials
and the form never appear in a URL, an error, or a log. No test touches the
network: the resolver and the httpx transport are fakes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from asap.utils.guarded_fetch import (
    GuardedFetchError,
    guarded_fetch_json,
    guarded_post_form_json,
    validate_guarded_url,
)

_PUBLIC_ADDRESS = "93.184.216.34"
_URL = "https://idp.example/oauth/introspect"
_SECRET = "client-s3cret-value"
_FORM = {"token": "opaque-access-token-value", "token_type_hint": "access_token"}


async def _public_resolver(hostname: str) -> list[str]:
    return [_PUBLIC_ADDRESS]


async def _post(
    handler: Callable[[httpx.Request], httpx.Response], url: str = _URL, **kwargs: Any
) -> dict[str, Any]:
    return await guarded_post_form_json(
        url,
        _FORM,
        basic_auth=("resource-server", _SECRET),
        max_bytes=1024,
        resolver=_public_resolver,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _assert_no_secret(error: GuardedFetchError) -> None:
    for value in (_SECRET, _FORM["token"]):
        assert value not in str(error)
        assert value not in repr(error.__cause__)


async def test_post_is_pinned_and_sends_the_form_with_basic_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"active": True})

    assert await _post(handler) == {"active": True}
    request = seen[0]
    assert request.method == "POST"
    assert request.url.host == _PUBLIC_ADDRESS
    assert request.headers["Host"] == "idp.example"
    assert request.extensions["sni_hostname"] == "idp.example"
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert request.headers["Authorization"].startswith("Basic ")
    assert parse_qs(request.content.decode()) == {k: [v] for k, v in _FORM.items()}
    assert _SECRET not in str(request.url) and _FORM["token"] not in str(request.url)


@pytest.mark.parametrize(
    ("status", "headers", "code"),
    [
        (307, {"Location": "https://169.254.169.254/"}, "redirect_refused"),
        (401, {}, "http_status"),
        (200, {"Content-Length": "4096"}, "too_large"),
    ],
)
async def test_bad_responses_are_refused_without_the_credentials(
    status: int, headers: dict[str, str], code: str
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(status, headers=headers, content=b"{}")

    with pytest.raises(GuardedFetchError) as caught:
        await _post(handler)
    assert caught.value.code == code
    assert len(calls) == 1
    _assert_no_secret(caught.value)


async def test_private_host_is_refused_before_the_post() -> None:
    calls: list[str] = []

    async def private_resolver(hostname: str) -> list[str]:
        return ["10.0.0.7"]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    with pytest.raises(GuardedFetchError) as caught:
        await guarded_post_form_json(
            _URL, _FORM, basic_auth=("rs", _SECRET), resolver=private_resolver,
            transport=httpx.MockTransport(handler),
        )  # fmt: skip
    assert caught.value.code == "host_not_public"
    assert calls == []


async def test_transport_error_hides_the_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused: {request.content!r}", request=request)

    with pytest.raises(GuardedFetchError) as caught:
        await _post(handler)
    assert caught.value.code == "transport_error"
    assert _SECRET not in str(caught.value) and _FORM["token"] not in str(caught.value)


@pytest.mark.parametrize("url", ["http://idp.example/introspect", "https://rs:pw@idp.example/i"])
async def test_url_without_https_or_with_user_info_is_refused(url: str) -> None:
    with pytest.raises(GuardedFetchError) as caught:
        await _post(lambda request: httpx.Response(200, json={}), url=url)
    assert caught.value.code in {"scheme_not_https", "invalid_url"}
    assert "pw" not in str(caught.value)


async def test_get_also_refuses_user_info_without_echoing_it() -> None:
    with pytest.raises(GuardedFetchError) as caught:
        await guarded_fetch_json("https://user:hunter2@keys.example/jwks.json")
    assert caught.value.code == "invalid_url"
    assert "hunter2" not in str(caught.value) and "hunter2" not in caught.value.url
    with pytest.raises(GuardedFetchError):
        validate_guarded_url("https://user@keys.example/jwks.json")
