"""Content-Encoding tokens are exact after the handler lowercases and strips.

``deflate`` is already rejected. It is not a prefix or suffix of ``gzip``,
``br``, or ``identity``, so a containment check still rejects it. A lookalike
such as ``gzipx`` or ``gzi`` would be treated as a supported encoding.
Padded ``gzip`` is accepted. ``test_gzip_case_insensitive_encoding`` covers
``GZIP`` with no surrounding whitespace, and the decompress helper covers
padding on its own. The handler path needs both.
"""

from __future__ import annotations

import gzip
from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi import HTTPException, Request

from asap.models.entities import Manifest
from asap.transport._request_handler import ASAPRequestHandler
from asap.transport.compression import decompress_payload, get_supported_encodings
from asap.transport.handlers import HandlerRegistry
from asap.transport.server import RegistryHolder

_PLAIN = b'{"ok":true}'
_GZIP = gzip.compress(_PLAIN)
# Neighbors a containment check would treat as gzip, br, or identity.
_LOOKALIKES = ("gzipx", "gzi", "zip", "identityx", "ident", "brr", "b")
_PADDED_GZIP = (" gzip ", "\tgzip\n", "\tGZIP\n")


class _EncodedBody:
    """Request stand-in with one body chunk and a content-encoding header."""

    def __init__(self, payload: bytes, encoding: str) -> None:
        self._payload = payload
        self.headers = {"content-encoding": encoding}

    async def stream(self) -> AsyncIterator[bytes]:
        yield self._payload


def _handler(manifest: Manifest) -> ASAPRequestHandler:
    """Handler whose encoding allow-list is the behavior under test."""
    return ASAPRequestHandler(RegistryHolder(HandlerRegistry()), manifest)


def _unsupported_detail(normalized: str) -> str:
    """415 detail produced for a normalized Content-Encoding token."""
    supported = ", ".join(get_supported_encodings())
    return f"Unsupported Content-Encoding: {normalized}. Supported: {supported}"


@pytest.mark.parametrize("encoding", _PADDED_GZIP)
@pytest.mark.asyncio
async def test_handler_accepts_padded_gzip(encoding: str, sample_manifest: Manifest) -> None:
    """Whitespace and case around gzip still decompress on the handler path."""
    request = _EncodedBody(_GZIP, encoding)
    body = await _handler(sample_manifest).parse_json_body(cast(Request, request))
    assert body == {"ok": True}


@pytest.mark.parametrize("encoding", _LOOKALIKES)
@pytest.mark.asyncio
async def test_handler_rejects_encoding_lookalikes(
    encoding: str,
    sample_manifest: Manifest,
) -> None:
    """A lookalike is 415 even when the body is valid gzip."""
    request = _EncodedBody(_GZIP, encoding)
    with pytest.raises(HTTPException) as exc_info:
        await _handler(sample_manifest).parse_json_body(cast(Request, request))
    assert exc_info.value.status_code == 415
    assert exc_info.value.detail == _unsupported_detail(encoding)


@pytest.mark.asyncio
async def test_handler_reports_the_normalized_lookalike(sample_manifest: Manifest) -> None:
    """Padding and case are folded, then the folded token is still rejected."""
    request = _EncodedBody(_GZIP, " GZIPX ")
    with pytest.raises(HTTPException) as exc_info:
        await _handler(sample_manifest).parse_json_body(cast(Request, request))
    assert exc_info.value.status_code == 415
    assert exc_info.value.detail == _unsupported_detail("gzipx")


@pytest.mark.parametrize("encoding", _LOOKALIKES)
def test_decompress_rejects_encoding_lookalikes(encoding: str) -> None:
    """The decoder rejects the same lookalikes with the original token."""
    with pytest.raises(ValueError) as exc_info:
        decompress_payload(_PLAIN, encoding)
    message = f"Unsupported Content-Encoding: {encoding}. Supported: gzip, br, identity"
    assert str(exc_info.value) == message
