"""Tests for ``WWWAuthenticateASAPMiddleware`` (CHAL-001)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.requests import Request

from asap.transport.challenge import (
    HTTP_UNAUTHORIZED,
    WWWAuthenticateASAPMiddleware,
    default_manifest_discovery_url,
    format_www_authenticate_asap,
    parse_www_authenticate_asap,
)

if TYPE_CHECKING:
    from asap.models.entities import Manifest


@pytest.fixture
def challenge_app(sample_manifest: Manifest) -> FastAPI:
    """Minimal app with challenge middleware on ``/asap/capability`` prefix."""
    app = FastAPI()

    @app.get("/asap/capability/list")
    async def cap_list() -> JSONResponse:
        return JSONResponse(status_code=HTTP_UNAUTHORIZED, content={"detail": "nope"})

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse(status_code=HTTP_UNAUTHORIZED, content={"detail": "nope"})

    app.add_middleware(
        WWWAuthenticateASAPMiddleware,
        default_discovery_url="https://agent.example/.well-known/asap/manifest.json",
        path_prefixes=("/asap/capability",),
    )
    return app


def test_format_and_parse_roundtrip() -> None:
    url = "https://ex.test/.well-known/asap/manifest.json"
    header = format_www_authenticate_asap(url)
    assert "ASAP" in header
    assert parse_www_authenticate_asap(header) == url


def test_format_and_parse_roundtrip_escapes_quotes_and_backslashes() -> None:
    url = r'https://ex.test/path?q="quoted"\backslash'
    header = format_www_authenticate_asap(url)

    assert parse_www_authenticate_asap(header) == url


def test_parse_www_authenticate_returns_none_when_missing() -> None:
    assert parse_www_authenticate_asap(None) is None
    assert parse_www_authenticate_asap("") is None
    assert parse_www_authenticate_asap('Bearer realm="x"') is None
    assert parse_www_authenticate_asap('ASAP realm="x"') is None


def test_default_manifest_discovery_url_builds_wellknown() -> None:
    assert default_manifest_discovery_url("https://api.example/v1/asap").endswith(
        "/.well-known/asap/manifest.json"
    )


def test_default_manifest_discovery_url_uses_origin_only() -> None:
    """The manifest URL is the endpoint origin plus the well-known path.

    A non-default port and an IPv6 literal stay in the authority. The ASAP
    path is not placed in front of ``/.well-known``.
    """
    assert (
        default_manifest_discovery_url("  https://api.example:8443/v1/asap  ")
        == "https://api.example:8443/.well-known/asap/manifest.json"
    )
    assert (
        default_manifest_discovery_url("https://[2001:db8::10]:9443/asap")
        == "https://[2001:db8::10]:9443/.well-known/asap/manifest.json"
    )


def test_default_manifest_discovery_url_invalid_raises() -> None:
    with pytest.raises(ValueError, match="Invalid ASAP endpoint"):
        default_manifest_discovery_url("not-a-url")


def test_challenge_middleware_path_matcher() -> None:
    app = FastAPI()

    @app.get("/x")
    async def x() -> JSONResponse:
        return JSONResponse(status_code=HTTP_UNAUTHORIZED, content={})

    app.add_middleware(
        WWWAuthenticateASAPMiddleware,
        default_discovery_url="https://d.test/m.json",
        path_matcher=lambda path: path.startswith("/x"),
    )
    r = TestClient(app).get("/x")
    assert "ASAP" in r.headers.get("WWW-Authenticate", "")


def test_challenge_middleware_appends_to_existing_www_authenticate() -> None:
    app = FastAPI()

    @app.get("/asap/capability/z")
    async def z() -> JSONResponse:
        resp = JSONResponse(status_code=HTTP_UNAUTHORIZED, content={})
        resp.headers["WWW-Authenticate"] = 'Bearer realm="asap"'
        return resp

    app.add_middleware(
        WWWAuthenticateASAPMiddleware,
        default_discovery_url="https://d.test/m.json",
        path_prefixes=("/asap/capability",),
    )
    www = TestClient(app).get("/asap/capability/z").headers.get("WWW-Authenticate", "")
    assert www.startswith("Bearer")
    assert "ASAP" in www


def test_challenge_middleware_adds_header_on_401(challenge_app: FastAPI) -> None:
    client = TestClient(challenge_app)
    r = client.get("/asap/capability/list")
    assert r.status_code == 401
    www = r.headers.get("WWW-Authenticate", "")
    assert "ASAP" in www
    assert "discovery=" in www


def test_challenge_middleware_skips_unmatched_paths(challenge_app: FastAPI) -> None:
    client = TestClient(challenge_app)
    r = client.get("/health")
    assert r.status_code == 401
    assert "ASAP" not in r.headers.get("WWW-Authenticate", "")


def test_challenge_middleware_applies_to_all_paths_when_no_prefix_configured() -> None:
    app = FastAPI()

    @app.get("/misc/protected")
    async def misc() -> JSONResponse:
        return JSONResponse(status_code=HTTP_UNAUTHORIZED, content={})

    app.add_middleware(
        WWWAuthenticateASAPMiddleware,
        default_discovery_url="https://d.test/m.json",
    )
    r = TestClient(app).get("/misc/protected")
    assert "ASAP" in r.headers.get("WWW-Authenticate", "")


def test_request_state_blank_override_keeps_default_discovery() -> None:
    """A blank or non-string override must not replace the configured discovery URL."""
    app = FastAPI()
    default_discovery = "https://default.test/m.json"

    @app.get("/asap/capability/x")
    async def capability_x(request: Request) -> JSONResponse:
        kind = request.headers.get("x-override-kind")
        if kind == "blank":
            request.state.asap_challenge_discovery_url = "  \t"
        elif kind == "padded":
            request.state.asap_challenge_discovery_url = "  https://override.test/m.json  "
        elif kind == "number":
            request.state.asap_challenge_discovery_url = 42
        return JSONResponse(status_code=401, content={})

    app.add_middleware(
        WWWAuthenticateASAPMiddleware,
        default_discovery_url=default_discovery,
        path_prefixes=("/asap/capability",),
    )
    client = TestClient(app)

    def discovery_for(kind: str) -> str | None:
        response = client.get("/asap/capability/x", headers={"x-override-kind": kind})
        return parse_www_authenticate_asap(response.headers.get("www-authenticate"))

    assert discovery_for("blank") == default_discovery
    assert discovery_for("padded") == "https://override.test/m.json"
    assert discovery_for("number") == default_discovery


def test_request_state_override_discovery() -> None:
    app = FastAPI()

    @app.get("/asap/capability/x")
    async def x(request: Request) -> JSONResponse:
        request.state.asap_challenge_discovery_url = "https://override.test/m.json"
        return JSONResponse(status_code=401, content={})

    app.add_middleware(
        WWWAuthenticateASAPMiddleware,
        default_discovery_url="https://default.test/m.json",
        path_prefixes=("/asap/capability",),
    )
    r = TestClient(app).get("/asap/capability/x")
    parsed = parse_www_authenticate_asap(r.headers.get("www-authenticate"))
    assert parsed == "https://override.test/m.json"
