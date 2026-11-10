"""Shared circuit breakers are keyed by the full base URL.

A prefix or case-insensitive key would open one host's circuit because a
different host failed.
"""

from __future__ import annotations

from asap.transport.circuit_breaker import CircuitBreakerRegistry, CircuitState

_EXACT_URL = "https://peer.example/asap"
_NEIGHBOR_URLS: tuple[str, ...] = (
    "https://peer.example/asap-extra",
    "https://peer.example/asa",
    "https://peer.example/asap/extra",
    "https://peer.example.evil/asap",
    "HTTPS://peer.example/asap",
    "https://peer.example/asap ",
    " https://peer.example/asap",
    "https://peer.example/asap/",
    "https://user:secret@peer.example/asap",
)


def test_circuit_breaker_registry_url_match_is_exact() -> None:
    """Neighbor URLs, a trailing slash, and userinfo stay on a closed breaker."""
    registry = CircuitBreakerRegistry()
    primary = registry.get_or_create(_EXACT_URL, threshold=1, timeout=60.0)
    primary.record_failure()
    assert primary.get_state() == CircuitState.OPEN

    for url in _NEIGHBOR_URLS:
        other = registry.get_or_create(url, threshold=5, timeout=60.0)
        assert other is not primary
        assert other.threshold == 5
        assert other.get_state() == CircuitState.CLOSED
        assert other.get_consecutive_failures() == 0

    again = registry.get_or_create(_EXACT_URL, threshold=99, timeout=1.0)
    assert again is primary
    assert again.threshold == 1
    assert again.get_state() == CircuitState.OPEN
