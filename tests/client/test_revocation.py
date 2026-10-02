"""Unit tests for asap.client.revocation module."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from asap.client.revocation import DEFAULT_REVOKED_URL, RevokedAgentsList, is_revoked


def test_default_revoked_url_points_at_asap_protocol_org() -> None:
    assert DEFAULT_REVOKED_URL == (
        "https://raw.githubusercontent.com/asap-protocol/asap-protocol/main/revoked_agents.json"
    )
    assert "adriannoes" not in DEFAULT_REVOKED_URL


@pytest.mark.asyncio
async def test_is_revoked_uses_default_url_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASAP_REVOKED_AGENTS_URL", raising=False)
    mock_response = MagicMock()
    mock_response.json.return_value = {"revoked": [], "version": "1.0"}
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.aclose = AsyncMock()

    with patch("asap.client.revocation.httpx.AsyncClient", return_value=mock_client):
        result = await is_revoked("urn:asap:agent:any", revoked_url=None, http_client=None)

    assert result is False
    mock_client.get.assert_called_once_with(DEFAULT_REVOKED_URL)
    mock_client.aclose.assert_called_once()


def test_revoked_agents_list_parses_valid_json() -> None:
    json_str = '{"revoked": [{"urn": "urn:asap:agent:x", "reason": "compromised", "revoked_at": "2025-01-01T00:00:00Z"}], "version": "1.0"}'
    parsed = RevokedAgentsList.model_validate_json(json_str)
    assert len(parsed.revoked) == 1
    assert parsed.revoked[0].urn == "urn:asap:agent:x"
    assert parsed.revoked[0].reason == "compromised"
    assert parsed.version == "1.0"


def test_revoked_agents_list_empty() -> None:
    parsed = RevokedAgentsList.model_validate({"revoked": [], "version": "1.0"})
    assert parsed.revoked == []
    assert parsed.version == "1.0"


def _empty_list_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"revoked": [], "version": "1.0"})


@pytest.mark.asyncio
async def test_is_revoked_empty_list_returns_false() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_empty_list_handler)) as client:
        result = await is_revoked(
            "urn:asap:agent:any",
            revoked_url="https://example.com/revoked.json",
            http_client=client,
        )
    assert result is False


@pytest.mark.asyncio
async def test_is_revoked_urn_in_list_returns_true() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "revoked": [
                    {
                        "urn": "urn:asap:agent:revoked-one",
                        "reason": "compromised",
                        "revoked_at": "2025-01-15T12:00:00Z",
                    }
                ],
                "version": "1.0",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
        result = await is_revoked(
            "urn:asap:agent:revoked-one",
            revoked_url="https://example.com/revoked.json",
            http_client=client,
        )
    assert result is True


@pytest.mark.asyncio
async def test_is_revoked_urn_not_in_list_returns_false() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "revoked": [
                    {
                        "urn": "urn:asap:agent:other",
                        "reason": "compromised",
                        "revoked_at": "2025-01-15T12:00:00Z",
                    }
                ],
                "version": "1.0",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
        result = await is_revoked(
            "urn:asap:agent:not-revoked",
            revoked_url="https://example.com/revoked.json",
            http_client=client,
        )
    assert result is False


@pytest.mark.asyncio
async def test_is_revoked_http_error_raises() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await is_revoked(
                "urn:asap:agent:x",
                revoked_url="https://example.com/revoked.json",
                http_client=client,
            )


@pytest.mark.asyncio
async def test_is_revoked_invalid_json_raises() -> None:
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not valid json {{{")

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
        with pytest.raises(ValueError):
            await is_revoked(
                "urn:asap:agent:x",
                revoked_url="https://example.com/revoked.json",
                http_client=client,
            )


@pytest.mark.asyncio
async def test_is_revoked_non_dict_payload_returns_false() -> None:
    """Resilience: JSON array or non-dict payload does not crash; returns False."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
        result = await is_revoked(
            "urn:asap:agent:any",
            revoked_url="https://example.com/revoked.json",
            http_client=client,
        )
    assert result is False


@pytest.mark.asyncio
async def test_is_revoked_matches_dict_entries_and_skips_junk() -> None:
    """Non-dict list entries must not raise or hide a real revoked URN."""

    def mixed_payload(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            200,
            json={
                "revoked": [
                    "not-a-dict",
                    None,
                    {
                        "urn": "urn:asap:agent:x",
                        "reason": "compromised",
                        "revoked_at": "2025-01-01T00:00:00Z",
                    },
                ],
                "version": "1.0",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(mixed_payload)) as client:
        matched = await is_revoked(
            "urn:asap:agent:x",
            revoked_url="https://example.com/revoked.json",
            http_client=client,
        )
        other = await is_revoked(
            "urn:asap:agent:other",
            revoked_url="https://example.com/revoked.json",
            http_client=client,
        )
    assert matched is True
    assert other is False


@pytest.mark.asyncio
async def test_is_revoked_creates_and_closes_client_when_none_passed() -> None:
    mock_response = MagicMock()
    mock_response.json.return_value = {"revoked": [], "version": "1.0"}
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.aclose = AsyncMock()

    with patch("asap.client.revocation.httpx.AsyncClient", return_value=mock_client):
        result = await is_revoked(
            "urn:asap:agent:any",
            revoked_url="https://example.com/revoked.json",
            http_client=None,
        )
    assert result is False
    mock_client.get.assert_called_once()
    mock_client.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_is_revoked_reuses_shared_client() -> None:
    call_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            200,
            json={
                "revoked": [
                    {
                        "urn": "urn:asap:agent:revoked-one",
                        "reason": "compromised",
                        "revoked_at": "2025-01-15T12:00:00Z",
                    }
                ],
                "version": "1.0",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
        r1 = await is_revoked(
            "urn:asap:agent:revoked-one",
            revoked_url="https://example.com/revoked.json",
            http_client=client,
        )
        r2 = await is_revoked(
            "urn:asap:agent:other",
            revoked_url="https://example.com/revoked.json",
            http_client=client,
        )
    assert r1 is True
    assert r2 is False
    assert call_count == 2
