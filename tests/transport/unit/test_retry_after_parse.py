"""Unit tests for transport-client ``Retry-After`` parsing.

Negative delta-seconds must stay unparseable. ``asyncio.sleep`` raises
``ValueError`` for a negative delay, so a bare ``float()`` parse would turn a
429 into a crash instead of exponential backoff.
"""

from __future__ import annotations

import httpx
import pytest

from asap.transport.client._helpers import _parse_retry_after


def _response_with_retry_after(value: str) -> httpx.Response:
    """Build a 429 whose only interesting header is ``Retry-After``."""
    return httpx.Response(status_code=429, headers={"Retry-After": value})


@pytest.mark.parametrize("raw", ["-1", "-0.5", " -1"])
def test_negative_delta_seconds_fall_back_to_backoff(raw: str) -> None:
    """A signed or padded negative delay is not a sleep interval.

    Example:
        >>> _parse_retry_after(response_with_retry_after("-1")) is None
        True
    """
    assert _parse_retry_after(_response_with_retry_after(raw)) is None


def test_zero_delta_seconds_is_immediate_retry() -> None:
    """``Retry-After: 0`` is a real delay of zero seconds, not a missing header.

    Example:
        >>> _parse_retry_after(response_with_retry_after("0")) == 0.0
        True
    """
    assert _parse_retry_after(_response_with_retry_after("0")) == 0.0
