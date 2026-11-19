"""The ASAP challenge scheme is a whole word, compared without case.

``asap discovery="..."`` is a challenge. ``notasap`` and ``asapx`` are not.
"""

from __future__ import annotations

from asap.transport.challenge import parse_www_authenticate_asap

# The scheme scan reads the whole header, so this URL must not contain the word asap.
_URL = "https://agent.example/.well-known/manifest.json"
_REJECTED: tuple[str, ...] = (
    f'notasap discovery="{_URL}"',
    f'asapx discovery="{_URL}"',
    f'xasap discovery="{_URL}"',
    f'ASAPEXTRA discovery="{_URL}"',
)


def test_asap_challenge_scheme_is_a_whole_word() -> None:
    """Lowercase and uppercase scheme tokens parse. Embedded copies do not."""
    assert parse_www_authenticate_asap(f'asap discovery="{_URL}"') == _URL
    assert parse_www_authenticate_asap(f'ASAP DISCOVERY="{_URL}"') == _URL
    for header in _REJECTED:
        assert parse_www_authenticate_asap(header) is None
