"""Tests for OpenAPI adapter factory (`create_from_openapi`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from asap.adapters.openapi.factory import create_from_openapi
from asap.adapters.openapi.spec_loader import OpenAPISpecError

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "openapi"


def _transport_json(data: dict[str, Any]) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=data)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_create_from_openapi_requires_exactly_one_spec_source() -> None:
    doc = {"openapi": "3.0.3", "info": {"title": "T", "version": "1"}, "paths": {}}
    transport = _transport_json(doc)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OpenAPISpecError, match="Exactly one of spec_url"):
            await create_from_openapi(spec_url=None, spec_path=None, http_client=client)
        with pytest.raises(OpenAPISpecError, match="Exactly one of spec_url"):
            await create_from_openapi(
                spec_url="https://a/x.json",
                spec_path=_FIXTURES / "minimal-3.0.3.json",
                http_client=client,
            )


@pytest.mark.asyncio
async def test_padded_absolute_server_url_is_not_joined_onto_the_spec_host() -> None:
    """A padded absolute ``servers[0].url`` stays that origin, slash trimmed.

    Leading spaces would otherwise fail the ``https://`` check and ``urljoin``
    the value onto the spec host, leaving a trailing space on the upstream base.
    """
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "servers": [{"url": "  https://api.example/v1/  "}],
        "paths": {
            "/p": {"get": {"operationId": "p", "responses": {"200": {"description": "ok"}}}},
        },
    }
    transport = _transport_json(payload)
    async with httpx.AsyncClient(transport=transport) as client:
        bundle = await create_from_openapi(
            spec_url="https://spec.example/o.json",
            http_client=client,
        )
    assert bundle.upstream_base_url == "https://api.example/v1"


@pytest.mark.asyncio
async def test_infer_upstream_from_servers_absolute_url() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "servers": [{"url": "https://api.example/v1"}],
        "paths": {
            "/p": {"get": {"operationId": "p", "responses": {"200": {"description": "ok"}}}},
        },
    }
    transport = _transport_json(payload)
    async with httpx.AsyncClient(transport=transport) as client:
        bundle = await create_from_openapi(
            spec_url="https://spec.example/o.json", http_client=client
        )
    assert bundle.upstream_base_url == "https://api.example/v1"
    assert bundle.manifest.capabilities.streaming is False


@pytest.mark.asyncio
async def test_infer_upstream_relative_url_joined_with_spec_url() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "servers": [{"url": "/v3"}],
        "paths": {
            "/p": {"get": {"operationId": "p", "responses": {"200": {"description": "ok"}}}},
        },
    }
    transport = _transport_json(payload)
    async with httpx.AsyncClient(transport=transport) as client:
        bundle = await create_from_openapi(
            spec_url="https://petstore.example/api/openapi.json",
            http_client=client,
        )
    assert bundle.upstream_base_url == "https://petstore.example/v3"


@pytest.mark.asyncio
async def test_relative_server_on_local_spec_requires_upstream_override() -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "servers": [{"url": "/api"}],
        "paths": {
            "/z": {"get": {"operationId": "z", "responses": {"200": {"description": "ok"}}}},
        },
    }
    tmp = _FIXTURES / "_tmp_factory_relative_server.json"
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    try:
        transport = httpx.MockTransport(lambda _r: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(OpenAPISpecError, match="Relative OpenAPI server URL"):
                await create_from_openapi(spec_path=tmp, http_client=client)
            bundle = await create_from_openapi(
                spec_path=tmp,
                http_client=client,
                upstream_base_url="https://origin.example",
            )
        assert bundle.upstream_base_url == "https://origin.example"
    finally:
        tmp.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_no_servers_requires_upstream_base_url() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "servers": [],
        "paths": {
            "/x": {"get": {"operationId": "x", "responses": {"200": {"description": "ok"}}}},
        },
    }
    transport = _transport_json(payload)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OpenAPISpecError, match="no `servers`"):
            await create_from_openapi(spec_url="https://ex/o.json", http_client=client)
        bundle = await create_from_openapi(
            spec_url="https://ex/o.json",
            http_client=client,
            upstream_base_url="https://forced.example",
        )
    assert bundle.upstream_base_url == "https://forced.example"


@pytest.mark.asyncio
async def test_empty_server_url_raises() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "servers": [{"url": "   "}],
        "paths": {},
    }
    transport = _transport_json(payload)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(OpenAPISpecError, match=r"servers\[0\]\.url"):
            await create_from_openapi(spec_url="https://ex/o.json", http_client=client)


@pytest.mark.asyncio
async def test_upstream_base_url_override_strips_trailing_slash() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "servers": [{"url": "https://ignored.example/"}],
        "paths": {
            "/a": {"get": {"operationId": "a", "responses": {"200": {"description": "ok"}}}},
        },
    }
    transport = _transport_json(payload)
    async with httpx.AsyncClient(transport=transport) as client:
        bundle = await create_from_openapi(
            spec_url="https://ex/o.json",
            http_client=client,
            upstream_base_url=" https://custom.example/base/ ",
        )
    assert bundle.upstream_base_url == "https://custom.example/base"


@pytest.mark.asyncio
async def test_manifest_streaming_true_when_operation_is_sse() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "servers": [{"url": "https://api.example"}],
        "paths": {
            "/events": {
                "get": {
                    "operationId": "subscribe",
                    "responses": {
                        "200": {
                            "description": "sse",
                            "content": {"text/event-stream": {"schema": {"type": "string"}}},
                        },
                    },
                },
            },
        },
    }
    transport = _transport_json(payload)
    async with httpx.AsyncClient(transport=transport) as client:
        bundle = await create_from_openapi(spec_url="https://ex/o.json", http_client=client)
    assert bundle.manifest.capabilities.streaming is True


@pytest.mark.asyncio
async def test_kwargs_unknown_keys_emit_warning() -> None:
    payload = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "servers": [{"url": "https://api.example"}],
        "paths": {
            "/p": {"get": {"operationId": "p", "responses": {"200": {"description": "ok"}}}},
        },
    }
    transport = _transport_json(payload)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.warns(
            UserWarning, match=r"create_from_openapi ignored unexpected keyword arguments"
        ):
            bundle = await create_from_openapi(
                spec_url="https://ex/o.json",
                http_client=client,
                _forward_compat_reserved=True,
            )
    assert bundle.upstream_base_url == "https://api.example"


def test_asap_package_lazy_exports_create_from_openapi() -> None:
    """Top-level ``asap.create_from_openapi`` is a lazy alias (RF-3)."""
    import asap as asap_mod

    assert asap_mod.create_from_openapi is create_from_openapi
