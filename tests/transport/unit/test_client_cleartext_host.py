"""ASAPClient cleartext exception is an exact localhost hostname allowlist."""

from __future__ import annotations

import pytest

from asap.transport.client import ASAPClient


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1@evil.example",
        "http://localhost@evil.example",
        "ws://127.0.0.1@evil.example/asap",
        "http://localhost.evil.example",
        "http://127.0.0.1.evil.example",
        "http://notlocalhost",
        "http://[::ffff:127.0.0.1]",
        "HTTP://example.com",
    ],
)
def test_cleartext_remote_host_requires_https(base_url: str) -> None:
    """Userinfo, lookalike hosts, and scheme case stay outside the localhost exception.

    ``urlparse`` puts userinfo before ``@`` and the real host after it. A check
    against ``netloc`` would treat ``127.0.0.1@evil.example`` as loopback.
    """
    with pytest.raises(ValueError, match="Encrypted transport") as exc_info:
        ASAPClient(base_url)
    message = str(exc_info.value)
    assert "Invalid URL scheme" not in message
    assert base_url in message


def test_uppercase_https_is_accepted() -> None:
    """An uppercase https scheme is still encrypted transport."""
    client = ASAPClient("HTTPS://example.com")
    assert client.base_url == "HTTPS://example.com"
    assert client.require_https is True


def test_uppercase_http_loopback_is_accepted() -> None:
    """Uppercase http to 127.0.0.1 stays inside the localhost exception."""
    client = ASAPClient("HTTP://127.0.0.1:9")
    assert client.base_url == "HTTP://127.0.0.1:9"
    assert client.require_https is True


def test_userinfo_does_not_hide_a_loopback_hostname() -> None:
    """Credentials in the URL do not change a loopback hostname."""
    client = ASAPClient("http://evil.example@127.0.0.1:9")
    assert client.base_url == "http://evil.example@127.0.0.1:9"
    assert client.require_https is True
