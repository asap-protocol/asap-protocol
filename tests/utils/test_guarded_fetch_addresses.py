"""Address checks of the guarded fetch helper: scheme, IP ranges, DNS."""

from __future__ import annotations

import socket

import pytest

from asap.utils.guarded_fetch import (
    AddressResolver,
    GuardedFetchError,
    is_public_ip_address,
    resolve_public_addresses,
    validate_guarded_url,
)


class _FakeResolver:
    """Named resolver fake: returns fixed addresses and records each lookup."""

    def __init__(self, addresses: list[str]) -> None:
        self.addresses = addresses
        self.lookups: list[str] = []

    async def __call__(self, hostname: str) -> list[str]:
        self.lookups.append(hostname)
        return self.addresses


async def _failing_resolver(hostname: str) -> list[str]:
    raise socket.gaierror(socket.EAI_NONAME, f"unknown host {hostname}")


@pytest.mark.parametrize(
    "address",
    ["10.0.0.1", "127.0.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1"]
    + ["::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "64:ff9b::7f00:1"],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    assert is_public_ip_address(address) is False


@pytest.mark.parametrize("address", ["93.184.216.34", "2606:4700::1111"])
def test_public_addresses_are_accepted(address: str) -> None:
    assert is_public_ip_address(address) is True


def test_http_scheme_is_refused() -> None:
    with pytest.raises(GuardedFetchError) as caught:
        validate_guarded_url("http://jwks.example.com/keys")
    assert caught.value.code == "scheme_not_https"


@pytest.mark.parametrize(
    "url",
    [
        "https://10.0.0.1/keys",
        "https://127.0.0.1/keys",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/keys",
        "https://localhost/keys",
        "https://localhost./keys",
        "https://idp.localhost/keys",
    ],
)
def test_private_loopback_and_link_local_hosts_are_refused(url: str) -> None:
    with pytest.raises(GuardedFetchError) as caught:
        validate_guarded_url(url)
    assert caught.value.code == "host_not_public"


def test_switch_allows_loopback_hosts() -> None:
    parsed = validate_guarded_url("https://127.0.0.1:8443/keys", allow_private_hosts=True)
    assert parsed.host == "127.0.0.1"


def test_switch_does_not_allow_http() -> None:
    with pytest.raises(GuardedFetchError) as caught:
        validate_guarded_url("http://127.0.0.1/keys", allow_private_hosts=True)
    assert caught.value.code == "scheme_not_https"


async def test_hostname_that_resolves_to_a_private_address_is_refused() -> None:
    resolver = _FakeResolver(["93.184.216.34", "10.0.0.5"])
    with pytest.raises(GuardedFetchError) as caught:
        await resolve_public_addresses(
            validate_guarded_url("https://jwks.example.com/keys"), resolver=resolver
        )
    assert caught.value.code == "host_not_public"
    assert "10.0.0.5" in str(caught.value)
    assert resolver.lookups == ["jwks.example.com"]


async def test_hostname_that_resolves_to_public_addresses_is_accepted() -> None:
    resolver = _FakeResolver(["93.184.216.34", "2606:4700::1111"])
    addresses = await resolve_public_addresses(
        validate_guarded_url("https://jwks.example.com/keys"), resolver=resolver
    )
    assert addresses == ["93.184.216.34", "2606:4700::1111"]


async def test_ip_literal_is_not_sent_to_the_resolver() -> None:
    resolver = _FakeResolver([])
    addresses = await resolve_public_addresses(
        validate_guarded_url("https://93.184.216.34/keys"), resolver=resolver
    )
    assert addresses == ["93.184.216.34"]
    assert resolver.lookups == []


async def test_switch_allows_a_hostname_that_resolves_to_loopback() -> None:
    url = validate_guarded_url("https://localhost/keys", allow_private_hosts=True)
    addresses = await resolve_public_addresses(
        url, allow_private_hosts=True, resolver=_FakeResolver(["127.0.0.1"])
    )
    assert addresses == ["127.0.0.1"]


@pytest.mark.parametrize("resolver", [_failing_resolver, _FakeResolver([])])
async def test_dns_failure_or_empty_answer_is_refused(resolver: AddressResolver) -> None:
    with pytest.raises(GuardedFetchError) as caught:
        await resolve_public_addresses(
            validate_guarded_url("https://jwks.example.com/keys"), resolver=resolver
        )
    assert caught.value.code == "dns_failed"
