"""Unit tests for OpenAPI upstream execution (mocked httpx)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest

from asap.adapters.openapi.capability_mapper import (
    OpenAPIExecutionKind,
    OpenAPICapability,
    map_openapi_to_capabilities,
)
from asap.adapters.openapi.handler import (
    OpenAPIInvocationError,
    OpenAPIPathParameterError,
    OpenAPIUpstreamHandler,
    UnknownOpenAPICapabilityError,
    create_openapi_task_handler,
    execute,
    index_capabilities,
)
from asap.adapters.openapi.spec_loader import load_spec
from asap.errors import FatalError, RecoverableError
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.models.envelope import Envelope

from tests.adapters.openapi.conftest import tmp_openapi_spec


@pytest.mark.asyncio
async def test_execute_get_path_and_query_via_mocked_httpx(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/pets/{petId}": {
                "get": {
                    "operationId": "getPet",
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "verbose",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "boolean"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    seen: dict[str, Any] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        assert request.url.path == "/pets/abc-1"
        assert request.url.params.get("verbose") == "false"
        return httpx.Response(200, json={"ok": True})

    with tmp_openapi_spec(tmp_path, raw, "get_proxy") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://upstream.test",
                capabilities=caps,
                http_client=client,
            )
            out = await handler.execute(
                "getPet",
                {"petId": "abc-1", "verbose": False},
                session=None,
            )
        assert out == {"ok": True}
        assert seen["method"] == "GET"
        assert str(httpx.URL(seen["url"]).host) == "upstream.test"


@pytest.mark.asyncio
async def test_execute_post_json_body_and_query_via_mocked_httpx(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/pets": {
                "post": {
                    "operationId": "createPet",
                    "parameters": [
                        {
                            "name": "dryRun",
                            "in": "query",
                            "schema": {"type": "boolean"},
                        },
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                    "required": ["name"],
                                },
                            },
                        },
                    },
                    "responses": {"201": {"description": "created"}},
                },
            },
        },
    }
    captured: dict[str, Any] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["query"] = dict(request.url.params)
        captured["json"] = json.loads(request.content.decode())
        assert request.url.path == "/pets"
        return httpx.Response(201, json={"id": 42})

    with tmp_openapi_spec(tmp_path, raw, "post_proxy") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://api.items",
                capabilities=caps,
                http_client=client,
            )
            out = await execute(
                handler,
                "createPet",
                {"dryRun": True, "name": "Neko"},
            )
        assert out == {"id": 42}
        assert captured["method"] == "POST"
        assert captured["query"].get("dryRun") == "true"
        assert captured["json"] == {"name": "Neko"}


@pytest.mark.asyncio
async def test_execute_unknown_capability_raises(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/x": {"get": {"operationId": "onlyOne", "responses": {"200": {"description": "ok"}}}},
        },
    }

    def transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with tmp_openapi_spec(tmp_path, raw, "unknown_cap") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://e.test",
                capabilities=caps,
                http_client=client,
            )
            with pytest.raises(UnknownOpenAPICapabilityError) as exc_info:
                await handler.execute("missingCapability", {})
        assert exc_info.value.capability_name == "missingCapability"


@pytest.mark.asyncio
async def test_execute_module_wrapper_delegates(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/ping": {"get": {"operationId": "ping", "responses": {"200": {"description": "ok"}}}},
        },
    }

    def transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    with tmp_openapi_spec(tmp_path, raw, "wrapper") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://p.test",
                capabilities=caps,
                http_client=client,
            )
            assert await execute(handler, "ping", {}) == {}


@pytest.mark.asyncio
async def test_resolve_headers_merged_with_openapi_header_params(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "parameters": [
                        {
                            "name": "X-Request-Label",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    seen: dict[str, Any] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["x_request_label"] = request.headers.get("x-request-label")
        seen["x_session"] = request.headers.get("x-session-ctx")
        return httpx.Response(200, json={"items": []})

    class _Session:
        token = "secret"

    with tmp_openapi_spec(tmp_path, raw, "resolve_merge") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)

        def resolve_headers(session: object | None) -> dict[str, str]:
            assert isinstance(session, _Session)
            return {
                "Authorization": f"Bearer {session.token}",
                "X-Session-Ctx": "host",
            }

        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://svc.test",
                capabilities=caps,
                http_client=client,
                resolve_headers=resolve_headers,
            )
            out = await handler.execute(
                "listItems",
                {"X-Request-Label": "job-1"},
                session=_Session(),
            )
        assert out == {"items": []}
        assert seen["authorization"] == "Bearer secret"
        assert seen["x_request_label"] == "job-1"
        assert seen["x_session"] == "host"


@pytest.mark.asyncio
async def test_resolve_headers_openapi_header_params_override_callback(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/r": {
                "get": {
                    "operationId": "r",
                    "parameters": [
                        {
                            "name": "X-Trace",
                            "in": "header",
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    seen: dict[str, Any] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        seen["x_trace"] = request.headers.get("x-trace")
        return httpx.Response(200, json={})

    with tmp_openapi_spec(tmp_path, raw, "resolve_override") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)

        def resolve_headers(_session: object | None) -> dict[str, str]:
            return {"X-Trace": "from-resolve", "Authorization": "Bearer z"}

        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://svc.test",
                capabilities=caps,
                http_client=client,
                resolve_headers=resolve_headers,
            )
            await handler.execute("r", {"X-Trace": "from-args"}, session=None)
        assert seen["x_trace"] == "from-args"


@pytest.mark.asyncio
async def test_resolve_headers_callback_failure_is_recoverable_unauthorized(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/z": {"get": {"operationId": "z", "responses": {"200": {"description": "ok"}}}},
        },
    }

    def transport_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with tmp_openapi_spec(tmp_path, raw, "resolve_fail") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)

        def resolve_headers(_session: object | None) -> dict[str, str]:
            raise PermissionError("missing OAuth token")

        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://svc.test",
                capabilities=caps,
                http_client=client,
                resolve_headers=resolve_headers,
            )
            with pytest.raises(RecoverableError, match="resolve_headers callback failed"):
                await handler.execute("z", {}, session=None)


@pytest.mark.asyncio
async def test_resolve_headers_invalid_return_type_is_recoverable(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/z": {"get": {"operationId": "z2", "responses": {"200": {"description": "ok"}}}},
        },
    }

    with tmp_openapi_spec(tmp_path, raw, "resolve_bad_type") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)

        def resolve_headers(_session: object | None) -> dict[str, str]:
            bad: object = [("only", "list")]
            return cast("dict[str, str]", bad)

        transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={}))
        async with httpx.AsyncClient(transport=transport) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://svc.test",
                capabilities=caps,
                http_client=client,
                resolve_headers=resolve_headers,
            )
            with pytest.raises(RecoverableError, match="resolve_headers must return dict"):
                await handler.execute("z2", {}, session=None)


@pytest.mark.asyncio
async def test_index_capabilities_duplicate_skill_id_raises() -> None:
    skill = Skill(id="dup", description="d")
    c1 = OpenAPICapability(
        skill=skill,
        http_method="get",
        path_template="/a",
        execution_kind=OpenAPIExecutionKind.SYNC,
    )
    c2 = OpenAPICapability(
        skill=skill,
        http_method="get",
        path_template="/b",
        execution_kind=OpenAPIExecutionKind.SYNC,
    )
    with pytest.raises(ValueError, match="Duplicate OpenAPI capability"):
        index_capabilities([c1, c2])


@pytest.mark.asyncio
async def test_execute_cookie_parameter_sets_cookie_header(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/c": {
                "get": {
                    "operationId": "withCookie",
                    "parameters": [
                        {
                            "name": "sid",
                            "in": "cookie",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    seen: dict[str, Any] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json={"ok": True})

    with tmp_openapi_spec(tmp_path, raw, "cookie_param") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://upstream.test",
                capabilities=caps,
                http_client=client,
            )
            await handler.execute("withCookie", {"sid": "abc123"}, session=None)
        assert seen.get("cookie") == "sid=abc123"


@pytest.mark.asyncio
async def test_execute_missing_path_parameter_raises(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/pets/{petId}": {
                "get": {
                    "operationId": "getPet",
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    with tmp_openapi_spec(tmp_path, raw, "missing_path") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200))
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://u.test",
                capabilities=caps,
                http_client=client,
            )
            with pytest.raises(OpenAPIPathParameterError, match="Missing path parameter"):
                await handler.execute("getPet", {}, session=None)


@pytest.mark.asyncio
async def test_execute_empty_path_parameter_string_raises(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/pets/{petId}": {
                "get": {
                    "operationId": "getPet",
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    with tmp_openapi_spec(tmp_path, raw, "empty_path_val") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200))
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://u.test",
                capabilities=caps,
                http_client=client,
            )
            with pytest.raises(OpenAPIPathParameterError, match="Invalid path parameter"):
                await handler.execute("getPet", {"petId": ""}, session=None)


@pytest.mark.asyncio
async def test_execute_whitespace_only_path_parameter_raises(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/pets/{petId}": {
                "get": {
                    "operationId": "getPet",
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    with tmp_openapi_spec(tmp_path, raw, "ws_path_val") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200))
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://u.test",
                capabilities=caps,
                http_client=client,
            )
            with pytest.raises(OpenAPIPathParameterError) as exc_info:
                await handler.execute("getPet", {"petId": " \t "}, session=None)
            assert "petId" in exc_info.value.invalid


@pytest.mark.asyncio
async def test_execute_none_path_parameter_raises(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/pets/{petId}": {
                "get": {
                    "operationId": "getPet",
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    with tmp_openapi_spec(tmp_path, raw, "none_path_val") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200))
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://u.test",
                capabilities=caps,
                http_client=client,
            )
            with pytest.raises(OpenAPIPathParameterError, match="Invalid path parameter"):
                await handler.execute(
                    "getPet",
                    cast("dict[str, Any]", {"petId": None}),
                    session=None,
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("pet_id", [".", ".."])
async def test_execute_dot_segment_path_parameter_raises(
    tmp_path: Path,
    pet_id: str,
) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/pets/{petId}": {
                "get": {
                    "operationId": "getPet",
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    spec_name = "dot_seg_dot" if pet_id == "." else "dot_seg_dotdot"
    with tmp_openapi_spec(tmp_path, raw, spec_name) as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200))
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://u.test/gateway",
                capabilities=caps,
                http_client=client,
            )
            with pytest.raises(OpenAPIPathParameterError) as exc_info:
                await handler.execute("getPet", {"petId": pet_id}, session=None)
            assert "petId" in exc_info.value.invalid


@pytest.mark.asyncio
async def test_execute_dotdot_path_params_do_not_call_upstream(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/v1/{resource}/{itemId}": {
                "get": {
                    "operationId": "getItem",
                    "parameters": [
                        {
                            "name": "resource",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "itemId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                },
            },
        },
    }
    seen: dict[str, int] = {"calls": 0}

    def transport_handler(_request: httpx.Request) -> httpx.Response:
        seen["calls"] += 1
        return httpx.Response(200, json={"ok": True})

    with tmp_openapi_spec(tmp_path, raw, "dotdot_escape") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        transport = httpx.MockTransport(transport_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://api.example.com/gateway",
                capabilities=caps,
                http_client=client,
            )
            with pytest.raises(OpenAPIPathParameterError, match="Invalid path parameter"):
                await handler.execute(
                    "getItem",
                    {"resource": "..", "itemId": ".."},
                    session=None,
                )
        assert seen["calls"] == 0


@pytest.mark.asyncio
async def test_execute_unexpected_argument_raises(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/z": {"get": {"operationId": "z", "responses": {"200": {"description": "ok"}}}},
        },
    }
    with tmp_openapi_spec(tmp_path, raw, "unexpected_arg") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200))
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://u.test",
                capabilities=caps,
                http_client=client,
            )
            with pytest.raises(OpenAPIInvocationError, match="Unexpected argument"):
                await handler.execute("z", {"extra": "nope"}, session=None)


@pytest.mark.asyncio
async def test_resolve_headers_non_string_values_are_recoverable(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/z": {"get": {"operationId": "z3", "responses": {"200": {"description": "ok"}}}},
        },
    }

    with tmp_openapi_spec(tmp_path, raw, "resolve_non_str") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)

        def resolve_headers(_session: object | None) -> dict[str, str]:
            return cast("dict[str, str]", {"Authorization": 123})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200))
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://svc.test",
                capabilities=caps,
                http_client=client,
                resolve_headers=resolve_headers,
            )
            with pytest.raises(RecoverableError, match="str keys and str values"):
                await handler.execute("z3", {}, session=None)


@pytest.mark.asyncio
async def test_execute_upstream_connection_error_is_recoverable(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/z": {"get": {"operationId": "z4", "responses": {"200": {"description": "ok"}}}},
        },
    }
    req = httpx.Request("GET", "https://upstream.test/z")
    with tmp_openapi_spec(tmp_path, raw, "conn_err") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(
            side_effect=httpx.ConnectError("unreachable", request=req),
        )
        handler = OpenAPIUpstreamHandler.from_capabilities(
            base_url="https://upstream.test",
            capabilities=caps,
            http_client=mock_http,
        )
        with pytest.raises(RecoverableError, match="Upstream request failed"):
            await handler.execute("z4", {}, session=None)


@pytest.mark.asyncio
async def test_execute_upstream_502_is_recoverable(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/z": {"get": {"operationId": "z5", "responses": {"200": {"description": "ok"}}}},
        },
    }
    with tmp_openapi_spec(tmp_path, raw, "502") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(502))
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://upstream.test",
                capabilities=caps,
                http_client=client,
            )
            with pytest.raises(RecoverableError, match="Upstream HTTP 502") as exc_info:
                await handler.execute("z5", {}, session=None)
            assert "body_snippet" not in exc_info.value.details


@pytest.mark.asyncio
async def test_execute_upstream_404_is_fatal(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/z": {"get": {"operationId": "z6", "responses": {"200": {"description": "ok"}}}},
        },
    }
    with tmp_openapi_spec(tmp_path, raw, "404") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        long_body = "x" * 500
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _r: httpx.Response(404, text=long_body))
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://upstream.test",
                capabilities=caps,
                http_client=client,
            )
            with pytest.raises(FatalError, match="Upstream HTTP 404") as fi:
                await handler.execute("z6", {}, session=None)
            snippet = fi.value.details.get("body_snippet", "") if fi.value.details else ""
            assert len(snippet) <= 200


@pytest.mark.asyncio
async def test_execute_non_json_response_wraps_text(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/z": {"get": {"operationId": "z7", "responses": {"200": {"description": "ok"}}}},
        },
    }
    with tmp_openapi_spec(tmp_path, raw, "plain_text") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    200, content=b"hello", headers={"content-type": "text/plain"}
                ),
            ),
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://upstream.test",
                capabilities=caps,
                http_client=client,
            )
            out = await handler.execute("z7", {}, session=None)
        assert out["_text"] == "hello"
        assert out["content_type"] == "text/plain"


@pytest.mark.asyncio
async def test_execute_json_array_body_wraps_under_json_key(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/z": {"get": {"operationId": "z8", "responses": {"200": {"description": "ok"}}}},
        },
    }
    with tmp_openapi_spec(tmp_path, raw, "json_arr") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    200,
                    json=[1, 2, 3],
                    headers={"content-type": "application/json"},
                ),
            ),
        ) as client:
            handler = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://upstream.test",
                capabilities=caps,
                http_client=client,
            )
            out = await handler.execute("z8", {}, session=None)
        assert out == {"_json": [1, 2, 3]}


@pytest.mark.asyncio
async def test_create_openapi_task_handler_returns_task_response_envelope(tmp_path: Path) -> None:
    raw = {
        "openapi": "3.0.3",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/ping": {"get": {"operationId": "ping", "responses": {"200": {"description": "ok"}}}},
        },
    }
    with tmp_openapi_spec(tmp_path, raw, "task_handler") as path:
        doc = await load_spec(path)
        caps = map_openapi_to_capabilities(doc)
        transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={"pong": True}))
        async with httpx.AsyncClient(transport=transport) as http_client:
            upstream = OpenAPIUpstreamHandler.from_capabilities(
                base_url="https://agent_upstream.test",
                capabilities=caps,
                http_client=http_client,
            )
            task_fn = create_openapi_task_handler(upstream)
            manifest = Manifest(
                id="urn:asap:agent:openapi-test",
                name="Adapter",
                version="1.0.0",
                description="d",
                capabilities=Capability(
                    asap_version="0.1",
                    skills=[Skill(id="ping", description="p")],
                    state_persistence=False,
                ),
                endpoints=Endpoint(asap="http://localhost:8000/asap"),
            )
            envelope_in = Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:caller",
                recipient=manifest.id,
                payload_type="task.request",
                payload={
                    "conversation_id": "conv-1",
                    "skill_id": "ping",
                    "input": {},
                },
            )
            envelope_out = await task_fn(envelope_in, manifest)
        assert envelope_out.payload_type == "task.response"
        assert envelope_out.payload_dict["result"] == {"pong": True}
        assert envelope_out.correlation_id == envelope_in.id


def test_openapi_path_parameter_error_constructor_is_pure() -> None:
    # Construction must NOT raise, even for ambiguous (both/neither) invocations.
    both = OpenAPIPathParameterError(path_template="/x/{a}", missing=["a"], invalid=["a"])
    neither = OpenAPIPathParameterError(path_template="/x/{a}", missing=[], invalid=[])
    assert both.path_template == "/x/{a}"
    assert neither.path_template == "/x/{a}"
    assert isinstance(both, OpenAPIPathParameterError)
    assert isinstance(neither, OpenAPIPathParameterError)


def test_openapi_path_parameter_error_factories_enforce_invariant() -> None:
    # The "exactly one / non-empty" invariant now lives at the call-site factories.
    with pytest.raises(ValueError, match="for_missing requires a non-empty"):
        OpenAPIPathParameterError.for_missing("/x/{a}", [])
    with pytest.raises(ValueError, match="for_invalid requires a non-empty"):
        OpenAPIPathParameterError.for_invalid("/x/{a}", [])
    # Valid factory calls still produce the expected messages/details.
    miss_err = OpenAPIPathParameterError.for_missing("/x/{a}", ["a"])
    assert "Missing path parameter" in str(miss_err)
    assert miss_err.missing == ["a"]
    inv_err = OpenAPIPathParameterError.for_invalid("/x/{a}", ["a"])
    assert "Invalid path parameter" in str(inv_err)
    assert inv_err.invalid == ["a"]


@pytest.mark.asyncio
async def test_execute_unsupported_param_in_raises_invocation_error() -> None:
    skill = Skill(
        id="badLoc",
        description="test",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "string", "x-openapi-param-in": "matrix"},
            },
        },
        output_schema={"type": "object"},
    )
    cap = OpenAPICapability(
        skill=skill,
        http_method="get",
        path_template="/items",
        execution_kind=OpenAPIExecutionKind.SYNC,
    )
    transport = httpx.MockTransport(lambda _r: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        handler = OpenAPIUpstreamHandler.from_capabilities(
            base_url="https://u.test",
            capabilities=[cap],
            http_client=client,
        )
        with pytest.raises(OpenAPIInvocationError, match="Unsupported OpenAPI parameter location"):
            await handler.execute("badLoc", {"x": "v"}, session=None)
