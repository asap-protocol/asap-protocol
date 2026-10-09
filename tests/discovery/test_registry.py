"""Tests for Lite Registry client (SD-11, ADR-15)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from asap.discovery.registry import (
    LiteRegistry,
    RegistryEntry,
    derive_registry_hardware_fields,
    discover_from_registry,
    find_by_hardware_class,
    find_by_id,
    find_by_inference_mode,
    find_by_io,
    find_by_skill,
    generate_registry_entry,
    reset_registry_cache,
)
from asap.models.entities import (
    Capability,
    Endpoint,
    HardwareCapability,
    InferenceCapability,
    LocalModelInfo,
    Manifest,
    Skill,
)
from asap.models.enums import HardwareClass, HardwareIoType, InferenceMode

SHELLCLAW_V1_REGISTRY_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "registry" / "shellclaw-v1.0-entry.json"
)

VALID_REGISTRY_JSON = """{
  "version": "1.0",
  "updated_at": "2026-02-07T00:00:00Z",
  "agents": [
    {
      "id": "urn:asap:agent:alpha",
      "name": "Alpha Agent",
      "description": "First test agent",
      "endpoints": {"http": "https://alpha.example.com/asap", "manifest": "https://alpha.example.com/.well-known/asap/manifest.json"},
      "skills": ["code_review", "summarization"],
      "asap_version": "1.1.0"
    },
    {
      "id": "urn:asap:agent:beta",
      "name": "Beta Agent",
      "description": "Second test agent",
      "endpoints": {"http": "https://beta.example.com/asap"},
      "skills": ["code_review"],
      "asap_version": "1.1.0"
    }
  ]
}"""

EDGE_AI_REGISTRY_JSON = """{
  "version": "1.0",
  "updated_at": "2026-05-24T00:00:00Z",
  "agents": [
    {
      "id": "urn:asap:agent:jetson",
      "name": "Jetson Agent",
      "description": "Edge accelerator with CUDA",
      "endpoints": {"http": "https://jetson.example.com/asap"},
      "skills": ["gpio_control"],
      "asap_version": "2.1.0",
      "hardware_class": "edge_accelerator",
      "inference_modes": ["cloud", "local_cuda"],
      "hardware_io": ["gpio", "i2c"]
    },
    {
      "id": "urn:asap:agent:rpi",
      "name": "RPi Agent",
      "description": "SBC with local CPU inference",
      "endpoints": {"http": "https://rpi.example.com/asap"},
      "skills": ["assistant"],
      "asap_version": "2.1.0",
      "hardware_class": "sbc",
      "inference_modes": ["cloud", "local_cpu"],
      "hardware_io": ["gpio", "i2c"]
    },
    {
      "id": "urn:asap:agent:cloud-only",
      "name": "Cloud Agent",
      "description": "No structured hardware fields",
      "endpoints": {"http": "https://cloud.example.com/asap"},
      "skills": ["summarization"],
      "asap_version": "2.1.0"
    }
  ]
}"""


@pytest.fixture(autouse=True)
def clear_registry_cache() -> None:
    """Clear module-level registry cache before each test for isolation."""
    reset_registry_cache()


class TestRegistrySchemaValidation:
    """RegistryEntry and LiteRegistry validate correctly."""

    def test_valid_registry_entry_parses(self) -> None:
        """Valid RegistryEntry dict parses and serializes to JSON."""
        data = {
            "id": "urn:asap:agent:example",
            "name": "Example",
            "description": "Example agent",
            "endpoints": {"http": "https://example.com/asap"},
            "skills": ["skill_a"],
            "asap_version": "1.1.0",
        }
        entry = RegistryEntry.model_validate(data)
        assert entry.id == data["id"]
        assert entry.name == data["name"]
        assert entry.model_dump_json()

    def test_valid_lite_registry_parses(self) -> None:
        """Valid LiteRegistry JSON parses."""
        reg = LiteRegistry.model_validate_json(VALID_REGISTRY_JSON)
        assert reg.version == "1.0"
        assert len(reg.agents) == 2
        assert reg.agents[0].id == "urn:asap:agent:alpha"

    def test_invalid_registry_entry_rejects_extra_fields(self) -> None:
        """RegistryEntry with extra fields raises ValidationError (extra='forbid')."""
        data = {
            "id": "urn:asap:agent:example",
            "name": "Example",
            "description": "Example",
            "endpoints": {"http": "https://example.com/asap"},
            "skills": [],
            "asap_version": "1.1.0",
            "unknown_field": "forbidden",
        }
        with pytest.raises(ValidationError):
            RegistryEntry.model_validate(data)

    def test_invalid_registry_entry_missing_required_raises(self) -> None:
        """RegistryEntry missing required field raises ValidationError."""
        data = {
            "id": "urn:asap:agent:example",
            "name": "Example",
            "endpoints": {"http": "https://example.com/asap"},
            "skills": [],
            "asap_version": "1.1.0",
        }
        with pytest.raises(ValidationError):
            RegistryEntry.model_validate(data)

    def test_registry_entry_accepts_online_check_false(self) -> None:
        """online_check=False accepted for seeded agents."""
        data = {
            "id": "urn:asap:agent:seed:agent-0",
            "name": "Seed Agent",
            "description": "Demo agent",
            "endpoints": {"http": "https://example.com/seed/asap"},
            "skills": ["code_review"],
            "asap_version": "1.1.0",
            "online_check": False,
        }
        entry = RegistryEntry.model_validate(data)
        assert entry.online_check is False

    def test_shellclaw_v1_fixture_validates(self) -> None:
        """ShellClaw v1.0 IssueOps registry entry (context doc §4) passes RegistryEntry."""
        data = json.loads(SHELLCLAW_V1_REGISTRY_FIXTURE.read_text(encoding="utf-8"))
        entry = RegistryEntry.model_validate(data)
        assert entry.id == "urn:asap:agent:shellclaw"
        assert entry.category == "Infrastructure"
        assert entry.built_with == "Other"
        assert entry.online_check is False
        assert entry.verification is None
        assert entry.endpoints["manifest"] == (
            "https://adriannoes.github.io/shellclaw/manifest.json"
        )
        assert "self-signed" not in entry.tags
        assert entry.hardware_class == "edge_accelerator"
        assert entry.inference_modes == ["cloud", "local_cuda"]
        assert entry.hardware_io == ["gpio", "i2c"]

    def test_registry_entry_with_category_tags(self) -> None:
        """RegistryEntry parses category and tags from dict."""
        data = {
            "id": "urn:asap:agent:test:bot",
            "name": "Test",
            "description": "x",
            "endpoints": {"http": "https://example.com"},
            "asap_version": "2.0",
            "category": "Coding",
            "tags": ["ai", "code_review"],
        }
        entry = RegistryEntry.model_validate(data)
        assert entry.category == "Coding"
        assert entry.tags == ["ai", "code_review"]

    def test_registry_entry_defaults_without_category_tags(self) -> None:
        """RegistryEntry defaults category and tags when omitted."""
        data = {
            "id": "urn:asap:agent:test:bot",
            "name": "Test",
            "description": "x",
            "endpoints": {"http": "https://example.com"},
            "asap_version": "2.0",
        }
        entry = RegistryEntry.model_validate(data)
        assert entry.category is None
        assert entry.tags == []

    def test_registry_entry_normalizes_category_case(self) -> None:
        """RegistryEntry normalizes known category values to canonical form (e.g. coding -> Coding)."""
        data = {
            "id": "urn:asap:agent:test:bot",
            "name": "Test",
            "description": "x",
            "endpoints": {"http": "https://example.com"},
            "asap_version": "2.0",
            "category": "coding",
            "tags": [],
        }
        entry = RegistryEntry.model_validate(data)
        assert entry.category == "Coding"

    def test_registry_entry_preserves_unknown_category(self) -> None:
        """Unknown categories are preserved, avoiding accidental data loss."""
        data = {
            "id": "urn:asap:agent:test:bot",
            "name": "Test",
            "description": "x",
            "endpoints": {"http": "https://example.com"},
            "asap_version": "2.0",
            "category": "Robotics",
        }
        entry = RegistryEntry.model_validate(data)
        assert entry.category == "Robotics"

    def test_registry_entry_blank_category_becomes_none(self) -> None:
        """Whitespace-only category values normalize to None (same as omitted)."""
        data = {
            "id": "urn:asap:agent:test:bot",
            "name": "Test",
            "description": "x",
            "endpoints": {"http": "https://example.com"},
            "asap_version": "2.0",
            "category": "   ",
        }
        entry = RegistryEntry.model_validate(data)
        assert entry.category is None

    def test_registry_entry_with_derived_hardware_fields(self) -> None:
        """RegistryEntry accepts hardware_class, inference_modes, hardware_io."""
        data = {
            "id": "urn:asap:agent:edge:jetson",
            "name": "Jetson",
            "description": "Edge agent",
            "endpoints": {"http": "https://edge.example/asap"},
            "asap_version": "2.1.0",
            "hardware_class": "edge_accelerator",
            "inference_modes": ["cloud", "local_cuda"],
            "hardware_io": ["gpio", "i2c"],
        }
        entry = RegistryEntry.model_validate(data)
        assert entry.hardware_class == "edge_accelerator"
        assert entry.inference_modes == ["cloud", "local_cuda"]
        assert entry.hardware_io == ["gpio", "i2c"]

    def test_registry_entry_defaults_without_hardware_fields(self) -> None:
        """Omitted hardware mirror fields default to None / empty lists."""
        data = {
            "id": "urn:asap:agent:test:bot",
            "name": "Test",
            "description": "x",
            "endpoints": {"http": "https://example.com"},
            "asap_version": "2.0",
        }
        entry = RegistryEntry.model_validate(data)
        assert entry.hardware_class is None
        assert entry.inference_modes == []
        assert entry.hardware_io == []


class TestDeriveRegistryHardwareFields:
    """derive_registry_hardware_fields maps manifest capabilities to registry kwargs."""

    def test_jetson_manifest_derives_shellclaw_values(self) -> None:
        """ShellClaw Jetson capabilities → edge_accelerator, cloud/local_cuda, gpio/i2c."""
        manifest = Manifest(
            id="urn:asap:agent:shellclaw-jetson-v1",
            name="ShellClaw Jetson",
            version="1.0.0",
            description="Edge agent on Jetson Orin Nano Super",
            capabilities=Capability(
                asap_version="2.1.0",
                skills=[Skill(id="assistant", description="Assistant")],
                hardware=HardwareCapability(
                    class_=HardwareClass.EDGE_ACCELERATOR,
                    model="jetson_orin_nano_super_8gb",
                    io=[HardwareIoType.GPIO, HardwareIoType.I2C],
                ),
                inference=InferenceCapability(
                    modes=[InferenceMode.CLOUD, InferenceMode.LOCAL_CUDA],
                ),
            ),
            endpoints=Endpoint(asap="https://shellclaw.example/asap"),
        )
        derived = derive_registry_hardware_fields(manifest)
        assert derived == {
            "hardware_class": "edge_accelerator",
            "inference_modes": ["cloud", "local_cuda"],
            "hardware_io": ["gpio", "i2c"],
        }

    def test_rpi_manifest_derives_shellclaw_values(self) -> None:
        """ShellClaw RPi capabilities → sbc, cloud/local_cpu, gpio/i2c."""
        manifest = Manifest(
            id="urn:asap:agent:shellclaw-rpi-v1-1",
            name="ShellClaw RPi",
            version="1.1.0",
            description="Edge agent on Raspberry Pi Zero 2 W",
            capabilities=Capability(
                asap_version="2.1.0",
                skills=[Skill(id="assistant", description="Assistant")],
                hardware=HardwareCapability(
                    class_=HardwareClass.SBC,
                    model="raspberry_pi_zero_2w",
                    io=[HardwareIoType.GPIO, HardwareIoType.I2C],
                ),
                inference=InferenceCapability(
                    modes=[InferenceMode.CLOUD, InferenceMode.LOCAL_CPU],
                ),
            ),
            endpoints=Endpoint(asap="https://shellclaw-rpi.example/asap"),
        )
        derived = derive_registry_hardware_fields(manifest)
        assert derived == {
            "hardware_class": "sbc",
            "inference_modes": ["cloud", "local_cpu"],
            "hardware_io": ["gpio", "i2c"],
        }

    @pytest.mark.parametrize(
        ("capabilities", "expected"),
        [
            (
                Capability(
                    asap_version="2.1.0",
                    skills=[Skill(id="gpio_control", description="GPIO")],
                    hardware=HardwareCapability(io=[HardwareIoType.GPIO]),
                ),
                {"hardware_io": ["gpio"]},
            ),
            (
                Capability(
                    asap_version="2.1.0",
                    skills=[Skill(id="assistant", description="Assistant")],
                    hardware=HardwareCapability(class_=HardwareClass.SBC),
                    inference=InferenceCapability(modes=[]),
                ),
                {"hardware_class": "sbc"},
            ),
            (
                Capability(
                    asap_version="2.1.0",
                    skills=[Skill(id="assistant", description="Assistant")],
                    inference=InferenceCapability(local_models=[LocalModelInfo(id="tinyllama")]),
                ),
                {},
            ),
        ],
    )
    def test_partial_profiles_derive_only_filterable_fields(
        self,
        capabilities: Capability,
        expected: dict[str, object],
    ) -> None:
        """Partial hardware/inference profiles only mirror filterable populated fields."""
        manifest = Manifest(
            id="urn:asap:agent:partial-edge",
            name="Partial Edge",
            version="1.0.0",
            description="Partial hardware profile",
            capabilities=capabilities,
            endpoints=Endpoint(asap="https://partial.example/asap"),
        )
        assert derive_registry_hardware_fields(manifest) == expected

    def test_manifest_without_hardware_returns_empty(self) -> None:
        """Manifests without hardware/inference omit derived keys."""
        manifest = Manifest(
            id="urn:asap:agent:plain",
            name="Plain",
            version="1.0.0",
            description="No edge profile",
            capabilities=Capability(
                asap_version="1.1.0",
                skills=[Skill(id="echo", description="Echo")],
            ),
            endpoints=Endpoint(asap="https://example.com/asap"),
        )
        assert derive_registry_hardware_fields(manifest) == {}

    def test_io_only_without_hardware_class(self) -> None:
        """Hardware I/O without class still mirrors hardware_io for registry filters."""
        manifest = Manifest(
            id="urn:asap:agent:gpio-only",
            name="GPIO Board",
            version="1.0.0",
            description="GPIO without class",
            capabilities=Capability(
                asap_version="2.1.0",
                skills=[Skill(id="gpio_control", description="GPIO")],
                hardware=HardwareCapability(
                    class_=None,
                    io=[HardwareIoType.GPIO],
                ),
            ),
            endpoints=Endpoint(asap="https://gpio.example/asap"),
        )
        assert derive_registry_hardware_fields(manifest) == {"hardware_io": ["gpio"]}

    def test_derives_class_without_io_list(self) -> None:
        """hardware.class without io[] still derives hardware_class."""
        manifest = Manifest(
            id="urn:asap:agent:sbc-only",
            name="SBC",
            version="1.0.0",
            description="SBC class only",
            capabilities=Capability(
                asap_version="2.1.0",
                skills=[Skill(id="assistant", description="Assistant")],
                hardware=HardwareCapability(class_=HardwareClass.SBC),
            ),
            endpoints=Endpoint(asap="https://example.com/asap"),
        )
        assert derive_registry_hardware_fields(manifest) == {"hardware_class": "sbc"}

    def test_empty_inference_modes_omits_key(self) -> None:
        """Inference capability with empty modes list does not emit inference_modes."""
        manifest = Manifest(
            id="urn:asap:agent:no-modes",
            name="No Modes",
            version="1.0.0",
            description="Inference block without modes",
            capabilities=Capability(
                asap_version="2.1.0",
                skills=[Skill(id="assistant", description="Assistant")],
                inference=InferenceCapability(modes=[]),
            ),
            endpoints=Endpoint(asap="https://example.com/asap"),
        )
        assert derive_registry_hardware_fields(manifest) == {}

    def test_inference_only_manifest(self) -> None:
        """Inference-only manifests mirror inference_modes without hardware_class."""
        manifest = Manifest(
            id="urn:asap:agent:cpu-only",
            name="CPU Inference",
            version="1.0.0",
            description="Local CPU only",
            capabilities=Capability(
                asap_version="2.1.0",
                skills=[Skill(id="assistant", description="Assistant")],
                inference=InferenceCapability(modes=[InferenceMode.LOCAL_CPU]),
            ),
            endpoints=Endpoint(asap="https://cpu.example/asap"),
        )
        assert derive_registry_hardware_fields(manifest) == {"inference_modes": ["local_cpu"]}

    def test_partial_manifest_round_trip_registry_filters(self) -> None:
        """Partial hardware manifest generates registry entry discoverable by I/O filter."""
        manifest = Manifest(
            id="urn:asap:agent:partial-io",
            name="Partial IO",
            version="1.0.0",
            description="GPIO without class",
            capabilities=Capability(
                asap_version="2.1.0",
                skills=[Skill(id="gpio_control", description="GPIO")],
                hardware=HardwareCapability(
                    class_=None,
                    io=[HardwareIoType.GPIO],
                ),
            ),
            endpoints=Endpoint(asap="https://partial.example/asap"),
        )
        entry = generate_registry_entry(manifest, {"http": "https://partial.example/asap"})
        reg = LiteRegistry(
            version="1.0",
            updated_at="2026-05-28T00:00:00Z",
            agents=[entry],
        )
        matches = find_by_io(reg, "gpio")
        assert len(matches) == 1
        assert matches[0].id == "urn:asap:agent:partial-io"
        assert matches[0].hardware_class is None

    def test_hardware_model_without_class_omits_hardware_class(self) -> None:
        """Model-only hardware profiles do not invent a hardware_class value."""
        manifest = Manifest(
            id="urn:asap:agent:model-only",
            name="Model only",
            version="1.0.0",
            description="Self-reported model without class enum",
            capabilities=Capability(
                asap_version="2.1.0",
                skills=[Skill(id="assistant", description="Assistant")],
                hardware=HardwareCapability(model="custom_board_rev_a"),
            ),
            endpoints=Endpoint(asap="https://edge.example/asap"),
        )
        assert derive_registry_hardware_fields(manifest) == {}


class TestDiscoverFromRegistry:
    """discover_from_registry fetches and parses from URL."""

    @pytest.mark.asyncio
    async def test_fetches_and_parses_from_mock_url(self) -> None:
        """discover_from_registry returns parsed LiteRegistry from mock URL."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=VALID_REGISTRY_JSON.encode())

        reg = await discover_from_registry(
            registry_url="https://test.example/registry.json",
            transport=httpx.MockTransport(mock_transport),
        )
        assert reg.version == "1.0"
        assert len(reg.agents) == 2
        assert reg.agents[0].name == "Alpha Agent"
        assert reg.agents[1].skills == ["code_review"]

    @pytest.mark.asyncio
    async def test_discover_does_not_follow_redirects(self) -> None:
        """A 302 is rejected by raise_for_status and its Location is not fetched."""
        seen: list[str] = []

        def mock_transport(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(
                status_code=302,
                headers={"Location": "http://169.254.169.254/latest/meta-data/"},
                content=b"nope",
            )

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await discover_from_registry(
                registry_url="https://registry.example/registry.json",
                transport=httpx.MockTransport(mock_transport),
            )

        assert exc_info.value.response.status_code == 302
        assert seen == ["https://registry.example/registry.json"]

    @pytest.mark.asyncio
    async def test_cache_used_second_call_does_not_fetch(self) -> None:
        """Second discover_from_registry for same URL returns cached result; no second HTTP call."""
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(status_code=200, content=VALID_REGISTRY_JSON.encode())

        url = "https://cache-test.example/registry.json"
        reg1 = await discover_from_registry(
            registry_url=url, transport=httpx.MockTransport(mock_transport)
        )
        reg2 = await discover_from_registry(
            registry_url=url, transport=httpx.MockTransport(mock_transport)
        )

        assert call_count == 1
        assert reg1.version == reg2.version
        assert len(reg1.agents) == len(reg2.agents)

    @pytest.mark.asyncio
    async def test_cache_expired_refetches(self) -> None:
        """An expired cache entry is evicted and fetched again."""
        call_count = 0

        def mock_transport(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(status_code=200, content=VALID_REGISTRY_JSON.encode())

        url = "https://cache-expired.example/registry.json"
        await discover_from_registry(
            registry_url=url,
            ttl_seconds=0,
            transport=httpx.MockTransport(mock_transport),
        )
        await discover_from_registry(
            registry_url=url,
            ttl_seconds=0,
            transport=httpx.MockTransport(mock_transport),
        )

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_calls_same_url_coalesce_fetch(self) -> None:
        """Concurrent same-URL calls overlap and still perform a single fetch."""
        call_count = 0
        first_fetch_started = asyncio.Event()
        release_first_fetch = asyncio.Event()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = VALID_REGISTRY_JSON

        async def delayed_get(url: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            first_fetch_started.set()
            await release_first_fetch.wait()
            return mock_response

        mock_client = AsyncMock()
        mock_client.get.side_effect = delayed_get

        mock_context_manager = AsyncMock()
        mock_context_manager.__aenter__.return_value = mock_client
        mock_context_manager.__aexit__.return_value = None

        url = "https://coalesced.example/registry.json"
        with patch(
            "asap.discovery.registry.httpx.AsyncClient",
            return_value=mock_context_manager,
        ) as mock_async_client:
            first_task = asyncio.create_task(
                discover_from_registry(registry_url=url, transport=None)
            )
            await first_fetch_started.wait()
            second_task = asyncio.create_task(
                discover_from_registry(registry_url=url, transport=None)
            )
            release_first_fetch.set()
            reg1, reg2 = await asyncio.gather(first_task, second_task)

        mock_async_client.assert_called_once()
        assert reg1.version == reg2.version
        assert len(reg1.agents) == len(reg2.agents)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_expired_cache_failed_refresh_does_not_poison_next_refresh(self) -> None:
        """A failed refresh on expired cache still allows a later successful refresh."""

        def ok_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=VALID_REGISTRY_JSON.encode())

        def fail_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=503, content=b"temporarily unavailable")

        url = "https://refresh-failure.example/registry.json"
        await discover_from_registry(
            registry_url=url,
            ttl_seconds=0,
            transport=httpx.MockTransport(ok_transport),
        )
        with pytest.raises(httpx.HTTPStatusError):
            await discover_from_registry(
                registry_url=url,
                ttl_seconds=0,
                transport=httpx.MockTransport(fail_transport),
            )
        refreshed = await discover_from_registry(
            registry_url=url,
            ttl_seconds=30,
            transport=httpx.MockTransport(ok_transport),
        )

        assert refreshed.version == "1.0"
        assert len(refreshed.agents) == 2

    @pytest.mark.asyncio
    async def test_without_transport_uses_default_async_client(self) -> None:
        """When transport is omitted, discover_from_registry builds a default AsyncClient."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = VALID_REGISTRY_JSON

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        mock_context_manager = AsyncMock()
        mock_context_manager.__aenter__.return_value = mock_client
        mock_context_manager.__aexit__.return_value = None

        url = "https://default-client.example/registry.json"
        with patch(
            "asap.discovery.registry.httpx.AsyncClient",
            return_value=mock_context_manager,
        ) as mock_async_client:
            reg = await discover_from_registry(registry_url=url, transport=None)

        assert reg.version == "1.0"
        assert len(reg.agents) == 2
        mock_async_client.assert_called_once()
        call_kwargs = mock_async_client.call_args.kwargs
        assert "transport" not in call_kwargs
        assert isinstance(call_kwargs["timeout"], httpx.Timeout)
        mock_client.get.assert_awaited_once_with(url)

    @pytest.mark.asyncio
    async def test_network_error_raised(self) -> None:
        """discover_from_registry raises on HTTP error (e.g. 500)."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=500, content=b"Internal Server Error")

        with pytest.raises(httpx.HTTPStatusError):
            await discover_from_registry(
                registry_url="https://fail.example/registry.json",
                transport=httpx.MockTransport(mock_transport),
            )

    @pytest.mark.asyncio
    async def test_invalid_json_raises_validation_error(self) -> None:
        """Response that is not valid LiteRegistry JSON raises ValidationError."""

        def mock_transport(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=b'{"not": "a valid registry"}')

        with pytest.raises(ValidationError):
            await discover_from_registry(
                registry_url="https://bad.example/registry.json",
                transport=httpx.MockTransport(mock_transport),
            )


class TestFindBySkill:
    """find_by_skill returns correct agents."""

    def test_find_by_skill_returns_matching_agents(self) -> None:
        """find_by_skill returns only agents that list the skill."""
        reg = LiteRegistry.model_validate_json(VALID_REGISTRY_JSON)
        code_review = find_by_skill(reg, "code_review")
        summarization = find_by_skill(reg, "summarization")
        missing = find_by_skill(reg, "nonexistent")

        assert len(code_review) == 2
        assert all("code_review" in e.skills for e in code_review)
        assert len(summarization) == 1
        assert summarization[0].id == "urn:asap:agent:alpha"
        assert len(missing) == 0


class TestFindByHardwareClass:
    """find_by_hardware_class returns correct agents."""

    def test_find_by_hardware_class_returns_matching_agents(self) -> None:
        """find_by_hardware_class returns only agents with that hardware_class."""
        reg = LiteRegistry.model_validate_json(EDGE_AI_REGISTRY_JSON)
        edge = find_by_hardware_class(reg, "edge_accelerator")
        sbc = find_by_hardware_class(reg, "sbc")
        missing = find_by_hardware_class(reg, "nonexistent")

        assert len(edge) == 1
        assert edge[0].id == "urn:asap:agent:jetson"
        assert len(sbc) == 1
        assert sbc[0].id == "urn:asap:agent:rpi"
        assert len(missing) == 0


class TestFindByInferenceMode:
    """find_by_inference_mode returns correct agents."""

    def test_find_by_inference_mode_returns_matching_agents(self) -> None:
        """find_by_inference_mode returns only agents that list the mode."""
        reg = LiteRegistry.model_validate_json(EDGE_AI_REGISTRY_JSON)
        cloud = find_by_inference_mode(reg, "cloud")
        cuda = find_by_inference_mode(reg, "local_cuda")
        cpu = find_by_inference_mode(reg, "local_cpu")
        missing = find_by_inference_mode(reg, "local_tensorrt")

        assert len(cloud) == 2
        assert {e.id for e in cloud} == {"urn:asap:agent:jetson", "urn:asap:agent:rpi"}
        assert len(cuda) == 1
        assert cuda[0].id == "urn:asap:agent:jetson"
        assert len(cpu) == 1
        assert cpu[0].id == "urn:asap:agent:rpi"
        assert len(missing) == 0


class TestFindByIo:
    """find_by_io returns correct agents."""

    def test_find_by_io_returns_matching_agents(self) -> None:
        """find_by_io returns only agents that list the I/O type."""
        reg = LiteRegistry.model_validate_json(EDGE_AI_REGISTRY_JSON)
        gpio = find_by_io(reg, "gpio")
        i2c = find_by_io(reg, "i2c")
        spi = find_by_io(reg, "spi")

        assert len(gpio) == 2
        assert {e.id for e in gpio} == {"urn:asap:agent:jetson", "urn:asap:agent:rpi"}
        assert len(i2c) == 2
        assert len(spi) == 0


class TestFindById:
    """find_by_id returns entry or None."""

    def test_find_by_id_returns_entry(self) -> None:
        """find_by_id returns the matching RegistryEntry."""
        reg = LiteRegistry.model_validate_json(VALID_REGISTRY_JSON)
        entry = find_by_id(reg, "urn:asap:agent:beta")
        assert entry is not None
        assert entry.name == "Beta Agent"

    def test_find_by_id_returns_none_when_missing(self) -> None:
        """find_by_id returns None when no agent has that id."""
        reg = LiteRegistry.model_validate_json(VALID_REGISTRY_JSON)
        assert find_by_id(reg, "urn:asap:agent:nonexistent") is None


class TestGenerateRegistryEntry:
    """generate_registry_entry builds valid RegistryEntry from Manifest."""

    def test_generates_valid_entry_from_manifest(self) -> None:
        """generate_registry_entry produces valid RegistryEntry with correct fields."""
        manifest = Manifest(
            id="urn:asap:agent:my-agent",
            name="My Agent",
            version="1.0.0",
            description="Does things",
            capabilities=Capability(
                asap_version="1.1.0",
                skills=[
                    Skill(id="skill_x", description="X"),
                    Skill(id="skill_y", description="Y"),
                ],
            ),
            endpoints=Endpoint(asap="https://example.com/asap", events=None),
        )
        endpoints = {
            "http": "https://example.com/asap",
            "manifest": "https://example.com/.well-known/asap/manifest.json",
        }
        entry = generate_registry_entry(manifest, endpoints)
        assert entry.id == manifest.id
        assert entry.name == manifest.name
        assert entry.description == manifest.description
        assert entry.endpoints == endpoints
        assert entry.skills == ["skill_x", "skill_y"]
        assert entry.asap_version == "1.1.0"
        assert entry.repository_url is None
        assert entry.documentation_url is None
        assert entry.built_with is None
        assert entry.model_dump_json()

    def test_generates_entry_with_optional_metadata(self) -> None:
        manifest = Manifest(
            id="urn:asap:agent:my-agent",
            name="My Agent",
            version="1.0.0",
            description="Does things",
            capabilities=Capability(
                asap_version="1.1.0",
                skills=[Skill(id="skill_x", description="X")],
            ),
            endpoints=Endpoint(asap="https://example.com/asap", events=None),
        )
        endpoints = {"http": "https://example.com/asap", "manifest": "https://example.com/m.json"}
        entry = generate_registry_entry(
            manifest,
            endpoints,
            repository_url="https://github.com/me/agent",
            documentation_url="https://docs.example.com/agent",
            built_with="CrewAI",
        )
        assert entry.repository_url == "https://github.com/me/agent"
        assert entry.documentation_url == "https://docs.example.com/agent"
        assert entry.built_with == "CrewAI"

    def test_whitespace_urls_resolve_to_none(self) -> None:
        """repository_url and documentation_url with only whitespace become None."""
        manifest = Manifest(
            id="urn:asap:agent:my-agent",
            name="My Agent",
            version="1.0.0",
            description="Does things",
            capabilities=Capability(
                asap_version="1.1.0",
                skills=[Skill(id="skill_x", description="X")],
            ),
            endpoints=Endpoint(asap="https://example.com/asap", events=None),
        )
        endpoints = {"http": "https://example.com/asap"}
        entry = generate_registry_entry(
            manifest,
            endpoints,
            repository_url="   ",
            documentation_url="\t\n  ",
        )
        assert entry.repository_url is None
        assert entry.documentation_url is None

    def test_whitespace_built_with_resolves_to_none(self) -> None:
        """built_with with only whitespace becomes None."""
        manifest = Manifest(
            id="urn:asap:agent:my-agent",
            name="My Agent",
            version="1.0.0",
            description="Does things",
            capabilities=Capability(
                asap_version="1.1.0",
                skills=[Skill(id="skill_x", description="X")],
            ),
            endpoints=Endpoint(asap="https://example.com/asap", events=None),
        )
        entry = generate_registry_entry(
            manifest,
            {"http": "https://example.com/asap"},
            built_with="  \t  ",
        )
        assert entry.built_with is None

    def test_generates_entry_with_category_tags(self) -> None:
        """generate_registry_entry with category/tags produces valid entry."""
        manifest = Manifest(
            id="urn:asap:agent:my-agent",
            name="My Agent",
            version="1.0.0",
            description="Does things",
            capabilities=Capability(
                asap_version="1.1.0",
                skills=[Skill(id="skill_x", description="X")],
            ),
            endpoints=Endpoint(asap="https://example.com/asap", events=None),
        )
        endpoints = {"http": "https://example.com/asap"}
        entry = generate_registry_entry(
            manifest,
            endpoints,
            category="Coding",
            tags=["ai", "code_review"],
        )
        assert entry.category == "Coding"
        assert entry.tags == ["ai", "code_review"]
        entry.model_dump_json()

    def test_generates_entry_with_derived_hardware_from_manifest(self) -> None:
        """generate_registry_entry mirrors manifest hardware/inference."""
        manifest = Manifest(
            id="urn:asap:agent:edge:jetson",
            name="Jetson",
            version="1.0.0",
            description="Edge",
            capabilities=Capability(
                asap_version="2.1.0",
                skills=[Skill(id="gpio_control", description="GPIO")],
                hardware=HardwareCapability(
                    class_=HardwareClass.EDGE_ACCELERATOR,
                    io=[HardwareIoType.GPIO, HardwareIoType.I2C],
                ),
                inference=InferenceCapability(
                    modes=[InferenceMode.CLOUD, InferenceMode.LOCAL_CUDA],
                ),
            ),
            endpoints=Endpoint(asap="https://edge.example/asap"),
        )
        entry = generate_registry_entry(
            manifest,
            {"http": "https://edge.example/asap"},
            built_with="  Other  ",
        )
        assert entry.hardware_class == "edge_accelerator"
        assert entry.inference_modes == ["cloud", "local_cuda"]
        assert entry.hardware_io == ["gpio", "i2c"]
        assert entry.built_with == "Other"
