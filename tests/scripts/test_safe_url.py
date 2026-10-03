"""Tests for scripts.lib.safe_url."""

from __future__ import annotations

import socket
from unittest.mock import patch

from scripts.lib.safe_url import is_safe_endpoint_url, is_safe_http_url


def _fake_public_getaddrinfo(
    host: str,
    port: object,
    family: int = 0,
    sock_type: int = 0,
    proto: int = 0,
    flags: int = 0,
) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    _ = (host, port, family, sock_type, proto, flags)
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


class TestIsSafeHttpUrl:
    def test_allows_http_for_issueops_compatible_urls(self) -> None:
        """IssueOps may fetch HTTP manifests; SSRF checks target host/IP, not TLS."""
        with patch("scripts.lib.safe_url.socket.getaddrinfo", _fake_public_getaddrinfo):
            assert is_safe_http_url("http://example.com/path") is True

    def test_blocks_localhost_hostname(self) -> None:
        assert is_safe_http_url("https://localhost/registry.json") is False

    def test_blocks_metadata_ip(self) -> None:
        assert is_safe_http_url("https://169.254.169.254/latest/meta-data/") is False

    @patch("scripts.lib.safe_url.socket.getaddrinfo", _fake_public_getaddrinfo)
    def test_allows_https_public_example(self) -> None:
        assert is_safe_http_url("https://example.com/registry.json") is True

    def test_blocks_non_http_scheme(self) -> None:
        with patch("scripts.lib.safe_url.socket.getaddrinfo", _fake_public_getaddrinfo):
            assert is_safe_http_url("ftp://example.com/file") is False

    def test_blocks_literal_private_ip_hostname(self) -> None:
        assert is_safe_http_url("https://10.0.0.1/") is False

    def test_blocks_ipv6_loopback_link_local_and_mapped_loopback(self) -> None:
        """Literal IPv6 loopback, link-local, and mapped loopback are not fetchable."""
        assert is_safe_http_url("https://[::1]/path") is False
        assert is_safe_http_url("https://[fe80::1]/") is False
        assert is_safe_http_url("https://[::ffff:127.0.0.1]/") is False

    def test_blocks_nat64_well_known_prefix_with_private_embedded_ipv4(self) -> None:
        assert is_safe_http_url("http://[64:ff9b::10.0.0.1]/") is False
        assert is_safe_http_url("http://[64:ff9b::a9fe:a9fe]/") is False

    @patch("scripts.lib.safe_url.socket.getaddrinfo", _fake_public_getaddrinfo)
    def test_allows_nat64_well_known_prefix_with_public_embedded_ipv4(self) -> None:
        assert is_safe_http_url("http://[64:ff9b::8.8.8.8]/") is True

    def test_public_ip_hostname_uses_dns_resolution_path(self) -> None:
        """Literal public IPs skip blocked-host set but still resolve via getaddrinfo."""
        with patch("scripts.lib.safe_url.socket.getaddrinfo", _fake_public_getaddrinfo):
            assert is_safe_http_url("https://93.184.216.34/path") is True

    def test_blocks_when_dns_resolves_to_private_ip(self) -> None:
        def _private(
            host: str,
            port: object,
            family: int = 0,
            sock_type: int = 0,
            proto: int = 0,
            flags: int = 0,
        ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            _ = (host, port, family, sock_type, proto, flags)
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.10", 0))]

        with patch("scripts.lib.safe_url.socket.getaddrinfo", _private):
            assert is_safe_http_url("https://example.com/") is False

    def test_blocks_when_dns_resolves_to_nat64_metadata_ipv4(self) -> None:
        def _nat64_metadata(
            host: str,
            port: object,
            family: int = 0,
            sock_type: int = 0,
            proto: int = 0,
            flags: int = 0,
        ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            _ = (host, port, family, sock_type, proto, flags)
            return [
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("64:ff9b::a9fe:a9fe", 0),
                )
            ]

        with patch("scripts.lib.safe_url.socket.getaddrinfo", _nat64_metadata):
            assert is_safe_http_url("https://example.com/") is False

    def test_blocks_when_dns_resolution_fails(self) -> None:
        def _fail(
            host: str,
            port: object,
            family: int = 0,
            sock_type: int = 0,
            proto: int = 0,
            flags: int = 0,
        ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
            _ = (host, port, family, sock_type, proto, flags)
            raise socket.gaierror("nxdomain")

        with patch("scripts.lib.safe_url.socket.getaddrinfo", _fail):
            assert is_safe_http_url("https://example.com/") is False


class TestIsSafeEndpointUrl:
    def test_blocks_metadata_http_endpoint(self) -> None:
        assert is_safe_endpoint_url("http://169.254.169.254/asap") is False

    def test_blocks_loopback_websocket_endpoint(self) -> None:
        assert is_safe_endpoint_url("ws://127.0.0.1/asap/events") is False
        assert is_safe_endpoint_url("wss://localhost/asap/events") is False

    @patch("scripts.lib.safe_url.socket.getaddrinfo", _fake_public_getaddrinfo)
    def test_allows_public_websocket_endpoint(self) -> None:
        assert is_safe_endpoint_url("wss://api.example.com/asap/events") is True

    @patch("scripts.lib.safe_url.socket.getaddrinfo", _fake_public_getaddrinfo)
    def test_allows_public_http_endpoint(self) -> None:
        assert is_safe_endpoint_url("https://example.com/asap") is True

    def test_blocks_nat64_websocket_endpoint_with_metadata_embedded_ipv4(self) -> None:
        assert is_safe_endpoint_url("wss://[64:ff9b::a9fe:a9fe]/asap/events") is False
