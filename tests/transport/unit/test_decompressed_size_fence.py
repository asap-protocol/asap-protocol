"""Decompressed bodies keep the same exclusive size cap as the raw stream.

``parse_json_body`` rejects a body only when its length is greater than
``max_request_size``. Gzip can hide that length until after inflate, so the
post-decompress check must use the same exclusive comparison. A cap that
becomes inclusive would refuse a legal maximum payload.
"""

from __future__ import annotations

import gzip
from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi import HTTPException, Request

from asap.models.entities import Manifest
from asap.transport._request_handler import ASAPRequestHandler
from asap.transport.handlers import HandlerRegistry
from asap.transport.server import RegistryHolder

# Trailing spaces stay valid JSON and compress well below the plaintext cap.
_PLAIN_AT_CAP = b'{"ok":true}' + b" " * 400


class _GzipBody:
    """Request stand-in that yields one gzip chunk and a content-encoding header."""

    def __init__(self, compressed: bytes) -> None:
        self._compressed = compressed
        self.headers = {"content-encoding": "gzip"}

    async def stream(self) -> AsyncIterator[bytes]:
        yield self._compressed


def _handler(manifest: Manifest, max_request_size: int) -> ASAPRequestHandler:
    """Build a handler whose only limit under test is the request size cap."""
    return ASAPRequestHandler(
        RegistryHolder(HandlerRegistry()),
        manifest,
        max_request_size=max_request_size,
    )


def _gzip_under_cap(plain: bytes, cap: int) -> bytes:
    """Return gzip bytes and fail if they would trip the compressed-size check."""
    compressed = gzip.compress(plain)
    if len(compressed) > cap:
        raise AssertionError(
            f"gzip payload is {len(compressed)} bytes; expected <= cap {cap} "
            "so the decompressed check is the one under test"
        )
    return compressed


@pytest.mark.asyncio
async def test_gzip_body_exactly_at_the_cap_is_parsed(sample_manifest: Manifest) -> None:
    """A decompressed length equal to the cap is still parsed."""
    cap = len(_PLAIN_AT_CAP)
    request = _GzipBody(_gzip_under_cap(_PLAIN_AT_CAP, cap))

    body = await _handler(sample_manifest, cap).parse_json_body(cast(Request, request))

    assert body == {"ok": True}


@pytest.mark.asyncio
async def test_gzip_body_one_byte_over_the_cap_is_rejected(sample_manifest: Manifest) -> None:
    """One plaintext byte past the cap is rejected after inflate, before JSON parsing."""
    cap = len(_PLAIN_AT_CAP)
    over = _PLAIN_AT_CAP + b" "
    request = _GzipBody(_gzip_under_cap(over, cap))

    with pytest.raises(HTTPException) as exc_info:
        await _handler(sample_manifest, cap).parse_json_body(cast(Request, request))

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == (
        f"Decompressed request size ({len(over)} bytes) exceeds maximum ({cap} bytes)"
    )
