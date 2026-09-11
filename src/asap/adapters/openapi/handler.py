"""Execute OpenAPI-derived capabilities by proxying to an upstream HTTP API (OA-001, OA-006)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeAlias
from urllib.parse import quote

import httpx

from asap.adapters.openapi.capability_mapper import OpenAPICapability
from asap.errors import (
    FatalError,
    RPC_CONNECTION_ERROR,
    RPC_HANDLER_NOT_FOUND,
    RPC_REMOTE_GENERIC,
    RecoverableError,
)
from asap.models.entities import Manifest
from asap.models.enums import TaskStatus
from asap.models.envelope import Envelope
from asap.models.ids import generate_id
from asap.models.payloads import TaskRequest, TaskResponse

# No circular dependency: asap.transport.challenge imports only asap.discovery and
# asap.models, never asap.adapters.openapi — safe to import at module top.
from asap.transport.challenge import format_www_authenticate_asap
from asap.transport.handlers import AsyncHandler

logger = logging.getLogger(__name__)

_UNKNOWN_CAPABILITY = "asap:adapters/openapi/unknown_capability"
_INVOCATION = "asap:adapters/openapi/invocation"
_PATH_PARAMS = "asap:adapters/openapi/path_parameters"
_UPSTREAM = "asap:adapters/openapi/upstream_connection"
_UPSTREAM_5XX = "asap:adapters/openapi/upstream_server_error"
_UPSTREAM_4XX = "asap:adapters/openapi/upstream_client_error"
_RESOLVE_HEADERS = "asap:adapters/openapi/resolve_headers"

# Bound upstream HTTP error payloads copied into FatalError/RecoverableError details to
# reduce accidental leakage of large or sensitive bodies (prefer server logs for triage).
_UPSTREAM_CLIENT_ERROR_BODY_MAX_LEN = 200
_DOT_PATH_SEGMENTS = frozenset({".", ".."})

ResolveHeaders: TypeAlias = Callable[[object | None], dict[str, str]]


class UnknownOpenAPICapabilityError(FatalError):
    """No operation was registered under the given capability name."""

    def __init__(self, capability_name: str) -> None:
        super().__init__(
            _UNKNOWN_CAPABILITY,
            f"Unknown OpenAPI capability {capability_name!r}.",
            {"capability_name": capability_name},
            rpc_code=RPC_HANDLER_NOT_FOUND,
        )
        self.capability_name = capability_name


class OpenAPIInvocationError(FatalError):
    """Caller arguments do not match the derived input schema."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            _INVOCATION,
            message,
            details,
            rpc_code=RPC_REMOTE_GENERIC,
        )


class OpenAPIPathParameterError(FatalError):
    """Path template could not be fully substituted from *args*.

    Construction is pure (side-effect-free); use :meth:`for_missing` or
    :meth:`for_invalid` to enforce the "exactly one of missing/invalid" invariant
    at the raise-site rather than from ``__init__``.
    """

    def __init__(
        self,
        *,
        path_template: str,
        missing: list[str] | None = None,
        invalid: list[str] | None = None,
    ) -> None:
        miss = list(missing) if missing else []
        inv = list(invalid) if invalid else []
        if miss:
            message = (
                f"Missing path parameter(s) for template {path_template!r}: {', '.join(miss)}."
            )
            details: dict[str, Any] = {"path_template": path_template, "missing": miss}
        elif inv:
            message = (
                f"Invalid path parameter value(s) for template {path_template!r}: "
                f"{', '.join(inv)} (must not be None, empty, whitespace-only, "
                f"or a '.'/'..' path segment)."
            )
            details = {"path_template": path_template, "invalid": inv}
        else:
            # No validation here: pure construction. Ambiguous invocations get a
            # neutral message; the invariant is enforced by the from_* factories.
            message = f"Path parameter error for template {path_template!r}."
            details = {"path_template": path_template}
        super().__init__(
            _PATH_PARAMS,
            message,
            details,
            rpc_code=RPC_REMOTE_GENERIC,
        )
        self.path_template = path_template
        self.missing = miss
        self.invalid = inv

    @classmethod
    def for_missing(cls, path_template: str, missing: list[str]) -> OpenAPIPathParameterError:
        """Build an error for unsubstituted path placeholders (requires non-empty *missing*)."""
        if not missing:
            raise ValueError("for_missing requires a non-empty missing= list.")
        return cls(path_template=path_template, missing=missing)

    @classmethod
    def for_invalid(cls, path_template: str, invalid: list[str]) -> OpenAPIPathParameterError:
        """Build an error for empty/None/whitespace/dot-segment path values."""
        if not invalid:
            raise ValueError("for_invalid requires a non-empty invalid= list.")
        return cls(path_template=path_template, invalid=invalid)

    @classmethod
    def for_static_dot_segment(cls, path_template: str) -> OpenAPIPathParameterError:
        """Build an error when the template itself contains a `.` / `..` segment.

        Used when there are no placeholders to name in :meth:`for_invalid`.
        """
        return cls(path_template=path_template, invalid=[path_template])


def index_capabilities(caps: Iterable[OpenAPICapability]) -> dict[str, OpenAPICapability]:
    """Index capabilities by skill id, detecting duplicates early."""
    out: dict[str, OpenAPICapability] = {}
    for item in caps:
        skill_id = item.skill.id
        if skill_id in out:
            msg = f"Duplicate OpenAPI capability id {skill_id!r}."
            raise ValueError(msg)
        out[skill_id] = item
    return out


def _param_props_from_object(obj: dict[str, Any], param_in: dict[str, str]) -> None:
    if obj.get("type") != "object" or "properties" not in obj:
        return
    for name, sub in obj["properties"].items():
        if isinstance(sub, dict):
            loc = sub.get("x-openapi-param-in")
            if isinstance(loc, str):
                param_in[name] = loc


def _collect_body_property_keys(part: dict[str, Any], sink: set[str]) -> None:
    if part.get("type") == "object" and "properties" in part:
        sink.update(part["properties"])
        return
    if "allOf" in part:
        for child in part["allOf"]:
            if isinstance(child, dict):
                _collect_body_property_keys(child, sink)


def _parse_input_schema_routing(
    input_schema: dict[str, Any] | None,
) -> tuple[dict[str, str], set[str]]:
    """Map parameter names to OpenAPI ``in`` and collect JSON body property names."""
    if input_schema is None:
        return {}, set()

    param_in: dict[str, str] = {}
    body_keys: set[str] = set()
    if "allOf" in input_schema:
        parts = input_schema["allOf"]
        if parts and isinstance(parts[0], dict):
            _param_props_from_object(parts[0], param_in)
        for part in parts[1:]:
            if isinstance(part, dict):
                _collect_body_property_keys(part, body_keys)
        return param_in, body_keys

    if input_schema.get("type") == "object" and "properties" in input_schema:
        for name, sub in input_schema["properties"].items():
            if not isinstance(sub, dict):
                continue
            loc = sub.get("x-openapi-param-in")
            if isinstance(loc, str):
                param_in[name] = loc
            else:
                body_keys.add(name)
        return param_in, body_keys

    return {}, set()


def _split_arguments_for_request(
    input_schema: dict[str, Any] | None,
    args: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], dict[str, Any] | None]:
    param_in, body_keys = _parse_input_schema_routing(input_schema)
    allowed = set(param_in) | set(body_keys)
    if input_schema is None and args:
        raise OpenAPIInvocationError(
            "Unexpected arguments for capability with no input schema.",
            details={"unexpected": sorted(args)},
        )
    extras = set(args) - allowed
    if extras:
        raise OpenAPIInvocationError(
            f"Unexpected argument name(s): {', '.join(sorted(extras))}.",
            details={"unexpected": sorted(extras), "allowed": sorted(allowed)},
        )

    path_vals: dict[str, Any] = {}
    query: dict[str, Any] = {}
    headers: dict[str, str] = {}
    cookie_pairs: list[str] = []
    body: dict[str, Any] = {}

    for key, val in args.items():
        if key in param_in:
            loc = param_in[key]
            if loc == "path":
                path_vals[key] = val
            elif loc == "query":
                query[key] = val
            elif loc == "header":
                headers[key] = str(val)
            elif loc == "cookie":
                cookie_pairs.append(f"{key}={val}")
            else:
                raise OpenAPIInvocationError(
                    f"Unsupported OpenAPI parameter location {loc!r} for {key!r}.",
                    details={"name": key, "in": loc},
                )
        else:
            body[key] = val

    if cookie_pairs:
        headers["Cookie"] = "; ".join(cookie_pairs)

    wants_json = bool(body_keys)
    json_body: dict[str, Any] | None = body if wants_json else None

    return path_vals, query, headers, json_body


def _merge_request_headers(
    resolve_first: Mapping[str, str] | None,
    operation: Mapping[str, str],
) -> dict[str, str] | None:
    """Merge *resolve_first* with OpenAPI header parameters *operation*.

    *operation* wins on duplicate keys so per-invocation header args can override
    injected defaults (e.g. tracing) when needed.
    """
    if not resolve_first and not operation:
        return None
    merged: dict[str, str] = {}
    if resolve_first:
        merged.update(dict(resolve_first))
    merged.update(operation)
    return merged


def _headers_from_resolve_callback(
    fn: ResolveHeaders,
    session: object | None,
) -> dict[str, str]:
    """Run *fn* and validate a ``dict[str, str]`` for upstream injection."""
    try:
        raw = fn(session)
    except Exception as exc:
        raise RecoverableError(
            _RESOLVE_HEADERS,
            f"resolve_headers callback failed: {exc!s}.",
            {
                "error_type": type(exc).__name__,
            },
            rpc_code=RPC_REMOTE_GENERIC,
        ) from exc
    if not isinstance(raw, dict):
        raise RecoverableError(
            _RESOLVE_HEADERS,
            "resolve_headers must return dict[str, str].",
            {"got_type": type(raw).__name__},
            rpc_code=RPC_REMOTE_GENERIC,
        )
    out: dict[str, str] = {}
    for key, val in raw.items():
        if not isinstance(key, str) or not isinstance(val, str):
            raise RecoverableError(
                _RESOLVE_HEADERS,
                "resolve_headers dict must use only str keys and str values.",
                {
                    "key_type": type(key).__name__,
                    "value_type": type(val).__name__,
                },
                rpc_code=RPC_REMOTE_GENERIC,
            )
        out[key] = val
    return out


def _is_invalid_path_param_value(value: object) -> bool:
    """Return True when *value* is None, blank, or a URL dot-segment (`.` / `..`).

    ``urllib.parse.quote(..., safe="")`` leaves RFC 3986 unreserved dots unencoded,
    so ``petId=".."`` would otherwise become ``/pets/..`` and climb out of the
    templated prefix (including a path on *base_url*).
    """
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return str(value) in _DOT_PATH_SEGMENTS


def _filled_path_has_dot_segment(path: str) -> bool:
    """Return True if *path* contains a `.` or `..` segment after substitution."""
    return any(segment in _DOT_PATH_SEGMENTS for segment in path.split("/"))


def _fill_path_template(path_template: str, path_params: Mapping[str, Any]) -> str:
    raw_names = re.findall(r"\{([^}]+)\}", path_template)
    names_order = list(dict.fromkeys(raw_names))
    missing = [n for n in names_order if n not in path_params]
    if missing:
        raise OpenAPIPathParameterError.for_missing(path_template, missing)
    invalid_names = [n for n in names_order if _is_invalid_path_param_value(path_params[n])]
    if invalid_names:
        raise OpenAPIPathParameterError.for_invalid(path_template, invalid_names)
    out = path_template
    for name in names_order:
        raw = path_params[name]
        out = out.replace("{" + name + "}", quote(str(raw), safe=""))
    if _filled_path_has_dot_segment(out):
        if names_order:
            raise OpenAPIPathParameterError.for_invalid(path_template, names_order)
        raise OpenAPIPathParameterError.for_static_dot_segment(path_template)
    return out


class OpenAPIUpstreamHandler:
    """Proxy ASAP capability calls to an upstream service described by OpenAPI."""

    def __init__(
        self,
        *,
        base_url: str,
        capabilities: dict[str, OpenAPICapability],
        http_client: httpx.AsyncClient,
        resolve_headers: ResolveHeaders | None = None,
        asap_challenge_discovery_url: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._capabilities = capabilities
        self._http_client = http_client
        self._resolve_headers = resolve_headers
        self._asap_challenge_discovery_url = asap_challenge_discovery_url

    @classmethod
    def from_capabilities(
        cls,
        *,
        base_url: str,
        capabilities: Iterable[OpenAPICapability],
        http_client: httpx.AsyncClient,
        resolve_headers: ResolveHeaders | None = None,
        asap_challenge_discovery_url: str | None = None,
    ) -> OpenAPIUpstreamHandler:
        """Build a handler from a list of :class:`OpenAPICapability` records."""
        return cls(
            base_url=base_url,
            capabilities=index_capabilities(capabilities),
            http_client=http_client,
            resolve_headers=resolve_headers,
            asap_challenge_discovery_url=asap_challenge_discovery_url,
        )

    async def execute(
        self,
        capability_name: str,
        args: Mapping[str, Any],
        session: object | None = None,
    ) -> dict[str, Any]:
        """Invoke *capability_name* on the upstream API with *args*.

        Args:
            capability_name: ``operationId`` (or path/method fallback id) from the spec.
            args: Keys must match the mapped input schema (path/query/header/cookie + JSON body).
            session: Passed to :attr:`resolve_headers` when configured (OA-009), for example
                Host-held OAuth context used to build ``Authorization``.

        Returns:
            Parsed JSON object for ``application/json`` responses; an empty dict for HTTP 204.

        Raises:
            UnknownOpenAPICapabilityError: *capability_name* is not registered.
            OpenAPIInvocationError: *args* are inconsistent with the input schema.
            OpenAPIPathParameterError: Path placeholders unchanged, missing arguments,
                or path values that are ``None`` / empty / whitespace-only / ``.`` / ``..``.
            RecoverableError: Network failure, HTTP 5xx from upstream, or *resolve_headers* failure.
            FatalError: HTTP 4xx from upstream.
        """
        cap = self._capabilities.get(capability_name)
        if cap is None:
            raise UnknownOpenAPICapabilityError(capability_name)

        schema = cap.skill.input_schema
        schema_dict = schema if isinstance(schema, dict) else None
        path_vals, query, headers, json_body = _split_arguments_for_request(schema_dict, args)
        resolved_path = _fill_path_template(cap.path_template, path_vals)
        if not resolved_path.startswith("/"):
            resolved_path = "/" + resolved_path
        url = f"{self._base_url}{resolved_path}"

        resolved_auth: dict[str, str] | None = None
        if self._resolve_headers is not None:
            resolved_auth = _headers_from_resolve_callback(self._resolve_headers, session)
        merged_headers = _merge_request_headers(resolved_auth, headers)

        try:
            response = await self._http_client.request(
                cap.http_method.upper(),
                url,
                params=query or None,
                headers=merged_headers,
                json=json_body,
            )
        except httpx.RequestError as exc:
            logger.warning(
                "OpenAPI upstream request failed capability_name=%r url=%r error=%s",
                capability_name,
                url,
                exc,
            )
            raise RecoverableError(
                _UPSTREAM,
                f"Upstream request failed: {exc!s}.",
                {"capability_name": capability_name, "url": url},
                rpc_code=RPC_CONNECTION_ERROR,
            ) from exc

        if response.status_code == 204:
            return {}

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            req_url = str(exc.request.url)
            # Client-facing details: omit upstream body snippets for 5xx (internal errors /
            # possible sensitive stack hints); truncate 4xx bodies to mitigate leakage while
            # retaining some operator context.
            if status >= 500:
                details: dict[str, Any] = {
                    "capability_name": capability_name,
                    "url": req_url,
                    "status_code": status,
                }
                logger.warning(
                    "OpenAPI upstream server error capability_name=%r url=%r status=%s",
                    capability_name,
                    req_url,
                    status,
                )
                raise RecoverableError(
                    _UPSTREAM_5XX,
                    f"Upstream HTTP {status}.",
                    details,
                    rpc_code=RPC_REMOTE_GENERIC,
                ) from exc
            snippet = exc.response.text[:_UPSTREAM_CLIENT_ERROR_BODY_MAX_LEN]
            details = {
                "capability_name": capability_name,
                "url": req_url,
                "status_code": status,
                "body_snippet": snippet,
            }
            if status == 401 and self._asap_challenge_discovery_url:
                details["_www_authenticate_asap"] = format_www_authenticate_asap(
                    self._asap_challenge_discovery_url,
                )
            logger.error(
                "OpenAPI upstream client error capability_name=%r url=%r status=%s",
                capability_name,
                req_url,
                status,
            )
            raise FatalError(
                _UPSTREAM_4XX,
                f"Upstream HTTP {status}.",
                details,
                rpc_code=RPC_REMOTE_GENERIC,
            ) from exc

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            data = response.json()
            if isinstance(data, dict):
                return data
            return {"_json": data}

        return {
            "_text": response.text,
            "_status_code": response.status_code,
            "content_type": content_type,
        }


async def execute(
    handler: OpenAPIUpstreamHandler,
    capability_name: str,
    args: Mapping[str, Any],
    session: object | None = None,
) -> dict[str, Any]:
    """Invoke :meth:`OpenAPIUpstreamHandler.execute` on *handler* (module-level wrapper)."""
    return await handler.execute(capability_name, args, session=session)


def create_openapi_task_handler(upstream: OpenAPIUpstreamHandler) -> AsyncHandler:
    """Build an async ``task.request`` handler that proxies to *upstream*.

    The nested ``openapi_task_request_handler`` always passes ``session=None`` into
    :meth:`OpenAPIUpstreamHandler.execute` today. OA-009 header resolution from Host-held
    context likely needs envelope- or TaskRequest-level session wiring in a future change.
    """

    async def openapi_task_request_handler(envelope: Envelope, manifest: Manifest) -> Envelope:
        """Translate ``task.request`` envelopes through *upstream* (``session`` is always ``None``)."""
        task_request = TaskRequest.model_validate(envelope.payload_dict)
        result = await upstream.execute(
            task_request.skill_id,
            task_request.input,
            session=None,
        )
        response_payload = TaskResponse(
            task_id=f"task_{generate_id()}",
            status=TaskStatus.COMPLETED,
            result=result,
        )
        return Envelope(
            asap_version=envelope.asap_version,
            sender=manifest.id,
            recipient=envelope.sender,
            payload_type="task.response",
            payload=response_payload.model_dump(),
            correlation_id=envelope.id,
            trace_id=envelope.trace_id,
        )

    return openapi_task_request_handler


__all__ = [
    "OpenAPIInvocationError",
    "OpenAPIPathParameterError",
    "OpenAPIUpstreamHandler",
    "ResolveHeaders",
    "UnknownOpenAPICapabilityError",
    "create_openapi_task_handler",
    "execute",
    "index_capabilities",
]
