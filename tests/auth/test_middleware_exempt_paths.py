"""``exempt_paths`` on ``OAuth2Middleware``.

A route that verifies its own bearer is listed by its exact path. Any other
spelling of that path still goes through the middleware and fails closed.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from joserfc import jwk
from starlette.testclient import TestClient

from asap.auth.middleware import OAuth2Middleware

_EXEMPT = "/asap/capability/execute"


async def _never_fetch(uri: str) -> jwk.KeySet:
    raise AssertionError("an exempt path must not reach the JWKS fetch")


def _app(**middleware_kwargs: object) -> FastAPI:
    app = FastAPI(redirect_slashes=False)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def echo(path: str) -> dict[str, str]:
        return {"reached": path}

    app.add_middleware(
        OAuth2Middleware,
        jwks_uri="https://auth.example.com/jwks.json",
        jwks_fetcher=_never_fetch,
        **middleware_kwargs,
    )
    return app


def test_exempt_path_reaches_the_route_without_a_bearer() -> None:
    with TestClient(_app(exempt_paths=frozenset({_EXEMPT}))) as client:
        response = client.post(_EXEMPT)
    assert response.status_code == 200
    assert response.json() == {"reached": _EXEMPT.lstrip("/")}


@pytest.mark.parametrize(
    "path",
    [
        f"{_EXEMPT}/",
        f"{_EXEMPT}x",
        f"{_EXEMPT}/child",
        "/asap/Capability/execute",
        "/asap/capability//execute",
        "/asap/capability/list",
        "/asap",
    ],
)
def test_other_spellings_still_need_a_bearer(path: str) -> None:
    with TestClient(_app(exempt_paths=frozenset({_EXEMPT}))) as client:
        response = client.post(path)
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_no_path_is_exempt_by_default() -> None:
    with TestClient(_app()) as client:
        response = client.post(_EXEMPT)
    assert response.status_code == 401
