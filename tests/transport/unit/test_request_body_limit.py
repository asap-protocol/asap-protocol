"""Streamed JSON bodies must stop once they cross the configured size cap."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi import HTTPException, Request

from asap.models.entities import Manifest
from asap.transport._request_handler import ASAPRequestHandler
from asap.transport.handlers import HandlerRegistry
from asap.transport.server import RegistryHolder


class _ChunkedBody:
    """Request stand-in that records how many body chunks ``parse_json_body`` pulls."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.reads = 0
        self.headers: dict[str, str] = {}

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.reads += 1
            yield chunk


def _handler(manifest: Manifest, max_request_size: int) -> ASAPRequestHandler:
    return ASAPRequestHandler(
        RegistryHolder(HandlerRegistry()),
        manifest,
        max_request_size=max_request_size,
    )


@pytest.mark.asyncio
async def test_parse_json_body_stops_reading_after_the_oversized_chunk(
    sample_manifest: Manifest,
) -> None:
    """A later chunk must not be read once the cumulative body exceeds the cap."""
    handler = _handler(sample_manifest, max_request_size=4)
    request = _ChunkedBody([b'{"a"', b":1}", b"unused"])

    with pytest.raises(HTTPException) as exc_info:
        await handler.parse_json_body(cast(Request, request))

    assert exc_info.value.status_code == 413
    assert request.reads == 2
    assert exc_info.value.detail == "Request size (7 bytes) exceeds maximum (4 bytes)"


@pytest.mark.asyncio
async def test_parse_json_body_accepts_a_body_exactly_at_the_size_cap(
    sample_manifest: Manifest,
) -> None:
    """The size check is exclusive: a body equal to the cap is still parsed."""
    handler = _handler(sample_manifest, max_request_size=2)
    request = _ChunkedBody([b"{", b"}"])

    body = await handler.parse_json_body(cast(Request, request))

    assert body == {}
    assert request.reads == 2
