"""Shared SSRF-safe URL checks for scripts (IssueOps, telemetry collectors).

Mirrors the IssueOps guard used by agent registration: blocks private IPs, loopback,
link-local targets, and cloud metadata hosts after optional DNS resolution.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "metadata.google.internal",
        "metadata.aws.internal",
        "169.254.169.254",
    }
)

# Map WebSocket schemes onto HTTP so host/IP checks reuse is_safe_http_url.
_WS_TO_HTTP_SCHEMES = {"ws": "http", "wss": "https"}

_NAT64_NETWORK = ipaddress.IPv6Network("64:ff9b::/96")


def _embedded_ipv4_in_ipv6(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """Return embedded IPv4 for NAT64 or ``::ffff:`` literals (mirrors web ``isBlockedIPv6``)."""
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    if addr in _NAT64_NETWORK:
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return None


def _literal_ip_is_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(addr, ipaddress.IPv4Address):
        return addr.is_private or addr.is_loopback or addr.is_link_local
    embedded = _embedded_ipv4_in_ipv6(addr)
    if embedded is not None:
        return _literal_ip_is_blocked(embedded)
    return addr.is_private or addr.is_loopback or addr.is_link_local


def is_safe_http_url(url: str) -> bool:
    """Return True if ``url`` may be fetched (HTTP/HTTPS) without obvious SSRF risk.

    Blocks non-http(s) schemes, blocked hostnames, literal private/link-local IPs,
    and hostnames whose DNS resolution includes private/link-local addresses.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = (parsed.hostname or "").lower()
    if hostname in _BLOCKED_HOSTS:
        return False
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        addr = None
    else:
        assert addr is not None
        if _literal_ip_is_blocked(addr):
            return False
    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        for _, _, _, _, sockaddr in resolved:
            resolved_addr = ipaddress.ip_address(sockaddr[0])
            if resolved_addr.is_private or resolved_addr.is_loopback or resolved_addr.is_link_local:
                return False
    except (socket.gaierror, ValueError, OSError):
        return False
    return True


def is_safe_endpoint_url(url: str) -> bool:
    """Return True if an agent HTTP or WebSocket endpoint is not a private target.

    IssueOps persists these URLs in ``registry.json``. SDK clients then connect
    without a second host check, so a metadata or loopback endpoint becomes
    consumer-side SSRF. WebSocket schemes are rewritten to HTTP first.

    Example:
        >>> is_safe_endpoint_url("http://169.254.169.254/asap")
        False
        >>> is_safe_endpoint_url("ws://127.0.0.1/events")
        False
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme in _WS_TO_HTTP_SCHEMES:
        url = parsed._replace(scheme=_WS_TO_HTTP_SCHEMES[scheme]).geturl()
    return is_safe_http_url(url)
