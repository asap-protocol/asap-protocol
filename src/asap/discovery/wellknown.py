"""Well-known URI handler for ASAP agent discovery (RFC 8615).

Serves GET /.well-known/asap/manifest.json and, when the server has a signed
A2A v1.0 Agent Card, GET /.well-known/agent-card.json (COMPAT-003), both with
HTTP caching (Cache-Control, ETag, If-None-Match) for discovery by other agents.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from asap.models.entities import Manifest

if TYPE_CHECKING:
    from asap.adapters.a2a import AgentCard

WELLKNOWN_MANIFEST_PATH = "/.well-known/asap/manifest.json"
"""Standard path for the ASAP agent manifest (RFC 8615)."""

WELLKNOWN_AGENT_CARD_PATH = "/.well-known/agent-card.json"
"""A2A v1.0 Agent Card path (A2A §8.2; IANA lists ``agent-card.json``)."""

CONTENT_TYPE_JSON = "application/json"
"""Content-Type for manifest JSON response."""

CACHE_MAX_AGE_SECONDS = 300
"""Default max-age for Cache-Control (5 minutes)."""

CACHE_CONTROL_VALUE = f"public, max-age={CACHE_MAX_AGE_SECONDS}"
"""Cache-Control header value for manifest responses."""

HEADER_ETAG = "etag"
HEADER_IF_NONE_MATCH = "if-none-match"
HEADER_CACHE_CONTROL = "cache-control"


def get_manifest_json(manifest: Manifest) -> dict[str, object]:
    """Return manifest as JSON-serializable dict for the well-known endpoint.

    Args:
        manifest: The agent's manifest.

    Returns:
        Dictionary suitable for JSON response (model_dump).
    """
    return manifest.model_dump(mode="json", by_alias=True)


def compute_manifest_etag(manifest: Manifest) -> str:
    """Compute a strong ETag for the manifest from its canonical JSON.

    Uses SHA-256 of sorted JSON so the same manifest always yields the same ETag.

    Args:
        manifest: The agent's manifest.

    Returns:
        ETag value without quotes (caller adds quotes in header if needed).
    """
    return _document_etag(get_manifest_json(manifest))


def _document_etag(document: Mapping[str, Any]) -> str:
    payload = json.dumps(document, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _etag_header_value(etag: str) -> str:
    """Format ETag for HTTP header (quoted)."""
    return f'"{etag}"'


async def get_manifest_response(manifest: Manifest, request: Request) -> Response:
    """Return FastAPI response for GET /.well-known/asap/manifest.json.

    Returns the agent manifest as JSON with Content-Type: application/json,
    Cache-Control: public, max-age=300, and ETag. If the request sends
    If-None-Match matching the current ETag, returns 304 Not Modified.

    Args:
        manifest: The agent's manifest.
        request: The incoming request (used for If-None-Match).

    Returns:
        JSONResponse (200) with body and caching headers, or Response (304) when
        client sent matching If-None-Match.
    """
    return _cached_json_response(get_manifest_json(manifest), request)


async def get_agent_card_response(card: AgentCard, request: Request) -> Response:
    """Return the response for GET /.well-known/agent-card.json.

    The same caching as :func:`get_manifest_response`: ``Content-Type:
    application/json`` (A2A v1.0 defines no media type for the card),
    ``Cache-Control: public, max-age=300``, and an ``ETag`` over the whole card,
    signatures included; a matching ``If-None-Match`` gets 304.

    Example:
        >>> await get_agent_card_response(signed_card, request)  # doctest: +SKIP
    """
    return _cached_json_response(card.model_dump(mode="json"), request)


def _cached_json_response(document: dict[str, Any], request: Request) -> Response:
    etag_header = _etag_header_value(_document_etag(document))
    if_none_match = request.headers.get(HEADER_IF_NONE_MATCH)

    # RFC 7232: If-None-Match can be a single value or comma-separated list
    match = if_none_match is not None and any(
        part.strip() == etag_header for part in if_none_match.split(",")
    )
    if match:
        return Response(
            status_code=304,
            headers={
                HEADER_ETAG: etag_header,
                HEADER_CACHE_CONTROL: CACHE_CONTROL_VALUE,
            },
        )

    return JSONResponse(
        content=document,
        media_type=CONTENT_TYPE_JSON,
        headers={
            HEADER_ETAG: etag_header,
            HEADER_CACHE_CONTROL: CACHE_CONTROL_VALUE,
        },
    )
