"""Tests for Agent and Manifest entity models."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from asap.models.entities import (
    Agent,
    AuthScheme,
    Capability,
    Endpoint,
    HardwareCapability,
    InferenceCapability,
    LocalModelInfo,
    Manifest,
    Skill,
    SLADefinition,
)
from asap.models.enums import HardwareClass, HardwareIoType, InferenceMode
from asap.models.ids import generate_id


class TestAgent:
    """Test suite for Agent entity model."""

    def test_agent_creation_minimal(self):
        """Test creating an Agent with minimal required fields."""
        agent = Agent(
            id="urn:asap:agent:test-v1",
            manifest_uri="https://example.com/.well-known/asap/manifest.json",
            capabilities=["task.execute"],
        )

        assert agent.id == "urn:asap:agent:test-v1"
        assert agent.manifest_uri == "https://example.com/.well-known/asap/manifest.json"
        assert agent.capabilities == ["task.execute"]

    def test_agent_creation_full(self):
        """Test creating an Agent with all fields."""
        agent = Agent(
            id="urn:asap:agent:research-v1",
            manifest_uri="https://agents.example.com/.well-known/asap/research-v1.json",
            capabilities=["task.execute", "state.persist", "mcp.tools"],
        )

        assert len(agent.capabilities) == 3
        assert "mcp.tools" in agent.capabilities

    def test_agent_is_immutable(self):
        """Test that Agent instances are immutable."""
        agent = Agent(
            id="urn:asap:agent:test",
            manifest_uri="https://example.com/manifest.json",
            capabilities=["task.execute"],
        )

        with pytest.raises(ValidationError, match="frozen"):
            agent.id = "new_id"

    def test_agent_json_schema(self):
        """Test that Agent generates valid JSON Schema."""
        schema = Agent.model_json_schema()

        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "manifest_uri" in schema["properties"]
        assert "capabilities" in schema["properties"]
        assert set(schema["required"]) == {"id", "manifest_uri", "capabilities"}

    def test_agent_serialization(self):
        """Test Agent serialization to JSON."""
        agent = Agent(
            id="urn:asap:agent:test",
            manifest_uri="https://example.com/manifest.json",
            capabilities=["task.execute", "state.persist"],
        )

        json_data = agent.model_dump()
        assert json_data["id"] == "urn:asap:agent:test"
        assert len(json_data["capabilities"]) == 2

    def test_agent_deserialization(self):
        """Test Agent deserialization from JSON."""
        data = {
            "id": "urn:asap:agent:test",
            "manifest_uri": "https://example.com/manifest.json",
            "capabilities": ["task.execute"],
        }

        agent = Agent.model_validate(data)
        assert agent.id == data["id"]
        assert agent.capabilities == data["capabilities"]

    def test_agent_urn_max_length_rejected(self) -> None:
        """Test that Agent rejects URN longer than MAX_URN_LENGTH (256)."""
        from asap.models.constants import MAX_URN_LENGTH

        long_name = "a" * (MAX_URN_LENGTH - len("urn:asap:agent:") + 1)
        long_urn = f"urn:asap:agent:{long_name}"

        with pytest.raises(ValidationError) as exc_info:
            Agent(
                id=long_urn,
                manifest_uri="https://example.com/manifest.json",
                capabilities=["task.execute"],
            )
        errors = exc_info.value.errors()
        assert any("256" in str(e.get("msg", "")) for e in errors)

    def test_agent_urn_invalid_characters_rejected(self) -> None:
        """Test that Agent rejects URN with invalid characters (e.g. uppercase, spaces)."""
        with pytest.raises(ValidationError):
            Agent(
                id="urn:asap:agent:InvalidName",
                manifest_uri="https://example.com/manifest.json",
                capabilities=["task.execute"],
            )
        with pytest.raises(ValidationError):
            Agent(
                id="urn:asap:agent:name with spaces",
                manifest_uri="https://example.com/manifest.json",
                capabilities=["task.execute"],
            )

    def test_agent_urn_unicode_characters_rejected(self) -> None:
        """Test that Agent rejects URN with unicode characters (i18n edge case)."""
        with pytest.raises(ValidationError):
            Agent(
                id="urn:asap:agent:研究エージェント",
                manifest_uri="https://example.com/manifest.json",
                capabilities=["task.execute"],
            )
        with pytest.raises(ValidationError):
            Agent(
                id="urn:asap:agent:agënt-tëst",
                manifest_uri="https://example.com/manifest.json",
                capabilities=["task.execute"],
            )

    def test_agent_urn_valid_at_max_length(self) -> None:
        """Test that Agent accepts URN at exactly MAX_URN_LENGTH characters."""
        from asap.models.constants import MAX_URN_LENGTH

        name_len = MAX_URN_LENGTH - len("urn:asap:agent:")
        valid_urn = "urn:asap:agent:" + "a" * name_len
        agent = Agent(
            id=valid_urn,
            manifest_uri="https://example.com/manifest.json",
            capabilities=["task.execute"],
        )
        assert len(agent.id) == MAX_URN_LENGTH


class TestSkill:
    """Test suite for Skill model."""

    def test_skill_creation(self):
        """Test creating a Skill with all fields."""
        skill = Skill(
            id="web_research",
            description="Search and synthesize information from the web",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        )

        assert skill.id == "web_research"
        assert "search" in skill.description.lower()
        assert skill.input_schema["type"] == "object"
        assert skill.output_schema["type"] == "object"

    def test_skill_optional_schemas(self):
        """Test that input/output schemas are optional."""
        skill = Skill(id="simple_skill", description="A simple skill")

        assert skill.input_schema is None
        assert skill.output_schema is None


class TestCapability:
    """Test suite for Capability model."""

    def test_capability_creation(self):
        """Test creating Capability with all fields."""
        capability = Capability(
            asap_version="0.1",
            skills=[Skill(id="web_research", description="Research skill")],
            state_persistence=True,
            streaming=True,
            mcp_tools=["web_search", "url_fetch"],
        )

        assert capability.asap_version == "0.1"
        assert len(capability.skills) == 1
        assert capability.state_persistence is True
        assert capability.streaming is True
        assert len(capability.mcp_tools) == 2

    def test_capability_optional_fields(self):
        """Test that optional fields default correctly."""
        capability = Capability(asap_version="0.1", skills=[])

        assert capability.state_persistence is False
        assert capability.streaming is False
        assert capability.mcp_tools == []
        assert capability.hardware is None
        assert capability.inference is None

    def test_capability_backward_compat_without_hardware_inference(self) -> None:
        """Capability without hardware/inference validates (pre-v2.4 manifests)."""
        data = {
            "asap_version": "0.1",
            "skills": [{"id": "research", "description": "Research skill"}],
            "state_persistence": True,
            "streaming": False,
            "mcp_tools": [],
        }
        capability = Capability.model_validate(data)
        assert capability.hardware is None
        assert capability.inference is None

    def test_capability_shellclaw_jetson_example_roundtrip(self) -> None:
        """ShellClaw Jetson capabilities example round-trips through Capability."""
        example_path = (
            Path(__file__).resolve().parents[2]
            / "schemas"
            / "examples"
            / "shellclaw-jetson-capabilities.json"
        )
        raw = json.loads(example_path.read_text(encoding="utf-8"))
        capability = Capability.model_validate(raw)
        assert capability.hardware is not None
        assert capability.hardware.class_ == HardwareClass.EDGE_ACCELERATOR
        assert capability.hardware.model == "jetson_orin_nano_super_8gb"
        assert capability.hardware.io == [HardwareIoType.GPIO, HardwareIoType.I2C]
        assert capability.inference is not None
        assert capability.inference.modes == [InferenceMode.CLOUD, InferenceMode.LOCAL_CUDA]
        assert len(capability.inference.local_models) == 1
        model_info = capability.inference.local_models[0]
        assert model_info.id == "Phi-3-mini-4k-instruct-Q4_K_M"
        assert model_info.quantization == "Q4_K_M"
        assert model_info.throughput_tokens_per_second is None
        restored = Capability.model_validate(capability.model_dump(mode="json"))
        assert restored == capability

    def test_capability_shellclaw_rpi_roundtrip(self) -> None:
        """ShellClaw RPi structured capabilities validate and round-trip."""
        data = {
            "asap_version": "2.1.0",
            "skills": [{"id": "assistant", "description": "Assistant"}],
            "hardware": {
                "class": "sbc",
                "model": "raspberry_pi_zero_2w",
                "io": ["gpio", "i2c"],
            },
            "inference": {
                "modes": ["cloud", "local_cpu"],
                "local_models": [
                    {
                        "id": "tinyllama-1.1b-chat-Q4_K_M",
                        "quantization": "Q4_K_M",
                    }
                ],
            },
        }
        capability = Capability.model_validate(data)
        assert capability.hardware is not None
        assert capability.hardware.class_ == HardwareClass.SBC
        assert capability.inference is not None
        assert capability.inference.modes == [InferenceMode.CLOUD, InferenceMode.LOCAL_CPU]

    def test_capability_rejects_unknown_hardware_field(self) -> None:
        """HardwareCapability forbids extra properties."""
        with pytest.raises(ValidationError):
            Capability.model_validate(
                {
                    "asap_version": "0.1",
                    "skills": [],
                    "hardware": {"class": "sbc", "unknown": True},
                }
            )

    def test_capability_rejects_invalid_hardware_class(self) -> None:
        """Closed hardware class enum rejects unknown values."""
        with pytest.raises(ValidationError):
            Capability.model_validate(
                {
                    "asap_version": "0.1",
                    "skills": [],
                    "hardware": {"class": "not_a_real_class"},
                }
            )

    def test_capability_rejects_invalid_hardware_io(self) -> None:
        """Closed hardware I/O enum rejects unknown values."""
        with pytest.raises(ValidationError):
            Capability.model_validate(
                {
                    "asap_version": "0.1",
                    "skills": [],
                    "hardware": {"io": ["spi_fake"]},
                }
            )

    def test_capability_rejects_invalid_inference_mode(self) -> None:
        """Closed inference mode enum rejects unknown values."""
        with pytest.raises(ValidationError):
            Capability.model_validate(
                {
                    "asap_version": "0.1",
                    "skills": [],
                    "inference": {"modes": ["local_tensorrt"]},
                }
            )


class TestHardwareCapability:
    """Tests for HardwareCapability nested model."""

    def test_all_fields_optional(self) -> None:
        """Empty hardware object validates."""
        hw = HardwareCapability.model_validate({})
        assert hw.class_ is None
        assert hw.model is None
        assert hw.io == []

    def test_model_length_limited_to_100_characters(self) -> None:
        """Hardware model names longer than 100 characters are rejected."""
        with pytest.raises(ValidationError):
            HardwareCapability.model_validate({"model": "x" * 101})


class TestInferenceCapability:
    """Tests for InferenceCapability and LocalModelInfo."""

    def test_local_model_throughput_optional(self) -> None:
        """Throughput is optional; when set must be non-negative."""
        info = LocalModelInfo(
            id="model-a",
            quantization="Q4_K_M",
            throughput_tokens_per_second=12.5,
        )
        assert info.throughput_tokens_per_second == 12.5

    def test_local_model_negative_throughput_rejected(self) -> None:
        """Negative throughput_tokens_per_second is rejected."""
        with pytest.raises(ValidationError):
            LocalModelInfo(
                id="model-a",
                throughput_tokens_per_second=-1.0,
            )

    def test_inference_defaults_empty_lists(self) -> None:
        """InferenceCapability defaults modes and local_models to empty lists."""
        inference = InferenceCapability.model_validate({})
        assert inference.modes == []
        assert inference.local_models == []

    def test_local_model_rejects_extra_properties(self) -> None:
        """LocalModelInfo forbids unknown fields in nested inference metadata."""
        with pytest.raises(ValidationError):
            LocalModelInfo.model_validate(
                {
                    "id": "model-a",
                    "quantization": "Q4_K_M",
                    "unknown": True,
                }
            )


class TestEndpoint:
    """Test suite for Endpoint model."""

    def test_endpoint_creation(self):
        """Test creating Endpoint with required and optional fields."""
        endpoint = Endpoint(
            asap="https://api.example.com/asap", events="wss://api.example.com/asap/events"
        )

        assert endpoint.asap == "https://api.example.com/asap"
        assert endpoint.events == "wss://api.example.com/asap/events"

    def test_endpoint_optional_events(self):
        """Test that events endpoint is optional."""
        endpoint = Endpoint(asap="https://api.example.com/asap")

        assert endpoint.asap == "https://api.example.com/asap"
        assert endpoint.events is None


class TestAuthScheme:
    """Test suite for AuthScheme model."""

    def test_auth_scheme_bearer_only(self):
        """Test AuthScheme with bearer token only."""
        auth = AuthScheme(schemes=["bearer"])

        assert auth.schemes == ["bearer"]
        assert auth.oauth2 is None

    def test_auth_scheme_with_oauth2(self):
        """Test AuthScheme with OAuth2 configuration."""
        auth = AuthScheme(
            schemes=["bearer", "oauth2"],
            oauth2={
                "authorization_url": "https://auth.example.com/authorize",
                "token_url": "https://auth.example.com/token",
                "scopes": ["asap:execute", "asap:read"],
            },
        )

        assert "oauth2" in auth.schemes
        assert auth.oauth2 is not None
        assert auth.oauth2["authorization_url"] == "https://auth.example.com/authorize"
        assert len(auth.oauth2["scopes"]) == 2


class TestSLADefinition:
    """Test suite for SLADefinition model (SLA schema in manifest)."""

    def test_sla_definition_all_fields(self) -> None:
        """SLADefinition accepts all fields."""
        sla = SLADefinition(
            availability="99.5%",
            max_latency_p95_ms=500,
            max_error_rate="1%",
            support_hours="24/7",
        )
        assert sla.availability == "99.5%"
        assert sla.max_latency_p95_ms == 500
        assert sla.max_error_rate == "1%"
        assert sla.support_hours == "24/7"

    def test_sla_definition_optional_fields_default_none(self) -> None:
        """SLADefinition has all fields optional; defaults are None."""
        sla = SLADefinition()
        assert sla.availability is None
        assert sla.max_latency_p95_ms is None
        assert sla.max_error_rate is None
        assert sla.support_hours is None

    def test_sla_definition_partial_fields(self) -> None:
        """SLADefinition accepts a subset of fields."""
        sla = SLADefinition(availability="99.9%", support_hours="business")
        assert sla.availability == "99.9%"
        assert sla.support_hours == "business"
        assert sla.max_latency_p95_ms is None
        assert sla.max_error_rate is None

    def test_sla_definition_negative_latency_rejected(self) -> None:
        """SLADefinition rejects negative max_latency_p95_ms."""
        with pytest.raises(ValidationError):
            SLADefinition(max_latency_p95_ms=-1)

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("availability", ""),
            ("availability", "   "),
            ("availability", "not-a-percent"),
            ("max_error_rate", ""),
            ("max_error_rate", "banana"),
        ],
    )
    def test_sla_definition_rejects_non_percentage_strings(
        self, field_name: str, value: str
    ) -> None:
        """Availability and error rate must match the percentage pattern.

        Empty and whitespace values are rejected too. Skipping the check when
        the string is falsy would accept ``""``.
        """
        with pytest.raises(ValidationError, match="Invalid percentage string"):
            SLADefinition(**{field_name: value})

    def test_sla_definition_zero_latency_accepted(self) -> None:
        """SLADefinition accepts zero max_latency_p95_ms (ge=0)."""
        sla = SLADefinition(max_latency_p95_ms=0)
        assert sla.max_latency_p95_ms == 0

    def test_sla_definition_json_schema(self) -> None:
        """SLADefinition generates JSON Schema with expected properties."""
        schema = SLADefinition.model_json_schema()
        assert schema["type"] == "object"
        assert "availability" in schema["properties"]
        assert "max_latency_p95_ms" in schema["properties"]
        assert "max_error_rate" in schema["properties"]
        assert "support_hours" in schema["properties"]

    def test_sla_definition_serialization_roundtrip(self) -> None:
        """SLADefinition round-trips via model_dump and model_validate."""
        sla = SLADefinition(
            availability="99.5%",
            max_latency_p95_ms=200,
            max_error_rate="0.5%",
            support_hours="24/7",
        )
        data = sla.model_dump()
        restored = SLADefinition.model_validate(data)
        assert restored.availability == sla.availability
        assert restored.max_latency_p95_ms == sla.max_latency_p95_ms


class TestManifest:
    """Test suite for Manifest entity model."""

    def test_manifest_creation_minimal(self):
        """Test creating a Manifest with minimal required fields."""
        manifest = Manifest(
            id="urn:asap:agent:test-v1",
            name="Test Agent",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://api.example.com/asap"),
        )

        assert manifest.id == "urn:asap:agent:test-v1"
        assert manifest.name == "Test Agent"
        assert manifest.version == "1.0.0"
        assert manifest.capabilities.asap_version == "0.1"
        assert manifest.supported_versions == ["2.2"]

    def test_manifest_creation_full(self):
        """Test creating a Manifest with all fields."""
        manifest = Manifest(
            id="urn:asap:agent:research-v1",
            name="Research Agent",
            version="1.0.0",
            description="Performs web research and summarization",
            capabilities=Capability(
                asap_version="0.1",
                skills=[
                    Skill(
                        id="web_research",
                        description="Search and synthesize information",
                        input_schema={"type": "object"},
                        output_schema={"type": "object"},
                    )
                ],
                state_persistence=True,
                streaming=True,
                mcp_tools=["web_search", "url_fetch"],
            ),
            endpoints=Endpoint(
                asap="https://api.example.com/asap", events="wss://api.example.com/asap/events"
            ),
            auth=AuthScheme(
                schemes=["bearer", "basic"],
            ),
            signature="eyJhbGciOiJFZDI1NTE5...",
        )

        assert manifest.name == "Research Agent"
        assert len(manifest.capabilities.skills) == 1
        assert manifest.capabilities.skills[0].id == "web_research"
        assert manifest.endpoints.events is not None
        assert manifest.auth is not None
        assert manifest.auth.schemes == ["bearer", "basic"]
        assert manifest.signature == "eyJhbGciOiJFZDI1NTE5..."

    def test_manifest_optional_fields(self):
        """Test that optional fields are None by default."""
        manifest = Manifest(
            id="urn:asap:agent:simple",
            name="Simple Agent",
            version="1.0.0",
            description="A simple agent",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://api.example.com/asap"),
        )

        assert manifest.auth is None
        assert manifest.signature is None
        assert manifest.sla is None
        assert manifest.supported_versions == ["2.2"]

    def test_manifest_supported_versions_custom(self) -> None:
        """Manifest accepts explicit supported_versions list."""
        manifest = Manifest(
            id="urn:asap:agent:multi-ver",
            name="Multi",
            version="1.0.0",
            description="Test",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://api.example.com/asap"),
            supported_versions=["2.1", "2.2"],
        )
        assert manifest.supported_versions == ["2.1", "2.2"]

    def test_manifest_urn_max_length_rejected(self) -> None:
        """Test that Manifest rejects agent id URN longer than MAX_URN_LENGTH (256)."""
        from asap.models.constants import MAX_URN_LENGTH

        long_name = "a" * (MAX_URN_LENGTH - len("urn:asap:agent:") + 1)
        long_urn = f"urn:asap:agent:{long_name}"

        with pytest.raises(ValidationError) as exc_info:
            Manifest(
                id=long_urn,
                name="Test",
                version="1.0.0",
                description="Test",
                capabilities=Capability(asap_version="0.1", skills=[]),
                endpoints=Endpoint(asap="https://example.com/asap"),
            )
        errors = exc_info.value.errors()
        assert any("256" in str(e.get("msg", "")) for e in errors)

    def test_manifest_is_immutable(self):
        """Test that Manifest instances are immutable."""
        manifest = Manifest(
            id="urn:asap:agent:test",
            name="Test",
            version="1.0.0",
            description="Test",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://example.com/asap"),
        )

        with pytest.raises(ValidationError, match="frozen"):
            manifest.name = "New Name"

    def test_manifest_json_schema(self):
        """Test that Manifest generates valid JSON Schema."""
        schema = Manifest.model_json_schema()

        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "name" in schema["properties"]
        assert "version" in schema["properties"]
        assert "capabilities" in schema["properties"]
        assert "endpoints" in schema["properties"]
        assert "supported_versions" in schema["properties"]

        required_fields = set(schema["required"])
        assert "id" in required_fields
        assert "name" in required_fields
        assert "version" in required_fields
        assert "description" in required_fields
        assert "capabilities" in required_fields
        assert "endpoints" in required_fields

    def test_manifest_serialization(self):
        """Test Manifest serialization to JSON."""
        manifest = Manifest(
            id="urn:asap:agent:test",
            name="Test Agent",
            version="1.0.0",
            description="Test",
            capabilities=Capability(
                asap_version="0.1", skills=[Skill(id="test_skill", description="Test")]
            ),
            endpoints=Endpoint(asap="https://example.com/asap"),
        )

        json_data = manifest.model_dump()
        assert json_data["id"] == "urn:asap:agent:test"
        assert json_data["name"] == "Test Agent"
        assert len(json_data["capabilities"]["skills"]) == 1
        assert json_data["supported_versions"] == ["2.2"]

    def test_manifest_deserialization(self):
        """Test Manifest deserialization from JSON."""
        data = {
            "id": "urn:asap:agent:test",
            "name": "Test Agent",
            "version": "1.0.0",
            "description": "A test agent",
            "capabilities": {
                "asap_version": "0.1",
                "skills": [{"id": "test_skill", "description": "Test skill"}],
                "state_persistence": False,
                "streaming": False,
                "mcp_tools": [],
            },
            "endpoints": {"asap": "https://example.com/asap"},
        }

        manifest = Manifest.model_validate(data)
        assert manifest.id == data["id"]
        assert manifest.name == data["name"]
        assert len(manifest.capabilities.skills) == 1
        assert manifest.sla is None
        assert manifest.supported_versions == ["2.2"]

    def test_manifest_with_sla(self) -> None:
        """Manifest accepts optional sla field (SLADefinition)."""
        manifest = Manifest(
            id="urn:asap:agent:with-sla",
            name="Agent With SLA",
            version="1.0.0",
            description="Agent with SLA guarantees",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://api.example.com/asap"),
            sla=SLADefinition(
                availability="99.5%",
                max_latency_p95_ms=500,
                max_error_rate="1%",
                support_hours="24/7",
            ),
        )
        assert manifest.sla is not None
        assert manifest.sla.availability == "99.5%"
        assert manifest.sla.max_latency_p95_ms == 500
        assert manifest.sla.support_hours == "24/7"

    def test_manifest_deserialization_with_sla(self) -> None:
        """Manifest deserializes from JSON including sla object."""
        data = {
            "id": "urn:asap:agent:test",
            "name": "Test Agent",
            "version": "1.0.0",
            "description": "A test agent",
            "capabilities": {
                "asap_version": "0.1",
                "skills": [{"id": "test_skill", "description": "Test skill"}],
                "state_persistence": False,
                "streaming": False,
                "mcp_tools": [],
            },
            "endpoints": {"asap": "https://api.example.com/asap"},
            "sla": {
                "availability": "99.9%",
                "max_latency_p95_ms": 200,
                "max_error_rate": "0.5%",
                "support_hours": "business",
            },
        }
        manifest = Manifest.model_validate(data)
        assert manifest.sla is not None
        assert manifest.sla.availability == "99.9%"
        assert manifest.sla.max_latency_p95_ms == 200
        assert manifest.sla.support_hours == "business"
        assert manifest.supported_versions == ["2.2"]

    def test_manifest_json_schema_includes_sla(self) -> None:
        """Manifest JSON Schema includes optional sla property."""
        schema = Manifest.model_json_schema()
        assert "sla" in schema["properties"]
        assert "sla" not in schema.get("required", [])


class TestShellClawManifestFixtures:
    """ShellClaw Jetson/RPi manifest fixtures."""

    def test_shellclaw_jetson_v1_fixture_loads(self) -> None:
        """shellclaw-jetson-v1.0.json parses as Manifest with hardware + inference."""
        fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "manifests"
        path = fixtures_dir / "shellclaw-jetson-v1.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = Manifest.model_validate(data)
        assert manifest.id == "urn:asap:agent:shellclaw"
        assert manifest.capabilities.hardware is not None
        assert manifest.capabilities.hardware.class_ == HardwareClass.EDGE_ACCELERATOR
        assert manifest.capabilities.inference is not None
        assert manifest.capabilities.inference.modes == [
            InferenceMode.CLOUD,
            InferenceMode.LOCAL_CUDA,
        ]

    def test_shellclaw_rpi_v1_1_fixture_loads(self) -> None:
        """shellclaw-rpi-v1.1.json parses as Manifest with SBC + local_cpu."""
        fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "manifests"
        path = fixtures_dir / "shellclaw-rpi-v1.1.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = Manifest.model_validate(data)
        assert manifest.id == "urn:asap:agent:shellclaw-rpi-v1-1"
        assert manifest.capabilities.hardware is not None
        assert manifest.capabilities.hardware.class_ == HardwareClass.SBC
        assert manifest.capabilities.inference is not None
        assert InferenceMode.LOCAL_CPU in manifest.capabilities.inference.modes


class TestManifestSLAFixture:
    """Test manifest fixture with SLA (manifest_with_sla.json)."""

    def test_manifest_with_sla_fixture_loads(self) -> None:
        """Fixture manifest_with_sla.json parses as Manifest with SLA."""
        import json
        from pathlib import Path

        fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures"
        path = fixtures_dir / "manifest_with_sla.json"
        if not path.exists():
            pytest.skip("Fixture manifest_with_sla.json not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = Manifest.model_validate(data)
        assert manifest.id == "urn:asap:agent:example-with-sla"
        assert manifest.sla is not None
        assert manifest.sla.availability == "99.5%"
        assert manifest.sla.max_latency_p95_ms == 500
        assert manifest.sla.max_error_rate == "1%"
        assert manifest.sla.support_hours == "24/7"
        assert manifest.supported_versions == ["2.2"]


class TestConversation:
    """Test suite for Conversation entity model."""

    def test_conversation_creation_minimal(self):
        """Test creating a Conversation with minimal required fields."""
        from datetime import datetime, timezone
        from asap.models.entities import Conversation

        conv_id = generate_id()
        created_at = datetime.now(timezone.utc)

        conversation = Conversation(
            id=conv_id,
            participants=["urn:asap:agent:coordinator", "urn:asap:agent:research-v1"],
            created_at=created_at,
        )

        assert conversation.id == conv_id
        assert len(conversation.participants) == 2
        assert conversation.created_at == created_at
        assert conversation.metadata is None

    def test_conversation_with_metadata(self):
        """Test creating a Conversation with metadata."""
        from datetime import datetime, timezone
        from asap.models.entities import Conversation

        conversation = Conversation(
            id=generate_id(),
            participants=["urn:asap:agent:a", "urn:asap:agent:b"],
            created_at=datetime.now(timezone.utc),
            metadata={"purpose": "quarterly_report_research", "ttl_hours": 72},
        )

        assert conversation.metadata is not None
        assert conversation.metadata.purpose == "quarterly_report_research"
        assert conversation.metadata.ttl_hours == 72

    def test_common_metadata_rejects_unknown_fields(self) -> None:
        """CommonMetadata forbids extra properties in conversation metadata."""
        from datetime import datetime, timezone

        from asap.models.entities import Conversation

        with pytest.raises(ValidationError) as exc_info:
            Conversation(
                id=generate_id(),
                participants=["urn:asap:agent:a", "urn:asap:agent:b"],
                created_at=datetime.now(timezone.utc),
                metadata={"purpose": "research", "unknown_key": "value"},
            )
        errors = exc_info.value.errors()
        assert errors[0]["loc"] == ("metadata", "unknown_key")

    def test_conversation_json_schema(self):
        """Test that Conversation generates valid JSON Schema."""
        from asap.models.entities import Conversation

        schema = Conversation.model_json_schema()

        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "participants" in schema["properties"]
        assert "created_at" in schema["properties"]
        assert set(schema["required"]) == {"id", "participants", "created_at"}


class TestTask:
    """Test suite for Task entity model."""

    def test_task_creation_minimal(self):
        """Test creating a Task with minimal required fields."""
        from datetime import datetime, timezone
        from asap.models.entities import Task

        task_id = generate_id()
        conv_id = generate_id()
        created_at = datetime.now(timezone.utc)

        task = Task(
            id=task_id,
            conversation_id=conv_id,
            status="submitted",
            created_at=created_at,
            updated_at=created_at,
        )

        assert task.id == task_id
        assert task.conversation_id == conv_id
        assert task.status == "submitted"
        assert task.parent_task_id is None
        assert task.progress is None

    def test_task_with_progress(self):
        """Test creating a Task with progress information."""
        from datetime import datetime, timezone
        from asap.models.entities import Task

        task = Task(
            id=generate_id(),
            conversation_id=generate_id(),
            status="working",
            progress={"percent": 45, "message": "Analyzing search results..."},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        assert task.status == "working"
        assert task.progress is not None
        assert task.progress["percent"] == 45
        assert "Analyzing" in task.progress["message"]

    def test_task_with_parent(self):
        """Test creating a Task with a parent task."""
        from datetime import datetime, timezone
        from asap.models.entities import Task

        parent_id = generate_id()
        task = Task(
            id=generate_id(),
            conversation_id=generate_id(),
            parent_task_id=parent_id,
            status="working",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        assert task.parent_task_id == parent_id

    def test_task_depth_default(self):
        """Test that Task defaults depth to 0 when not provided."""
        from datetime import datetime, timezone
        from asap.models.entities import Task

        task = Task(
            id=generate_id(),
            conversation_id=generate_id(),
            status="submitted",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert task.depth == 0

    def test_task_depth_valid(self):
        """Test that Task accepts depth in range [0, MAX_TASK_DEPTH]."""
        from datetime import datetime, timezone
        from asap.models.constants import MAX_TASK_DEPTH
        from asap.models.entities import Task

        for d in (0, 1, MAX_TASK_DEPTH):
            task = Task(
                id=generate_id(),
                conversation_id=generate_id(),
                status="submitted",
                depth=d,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            assert task.depth == d

    def test_task_depth_exceeds_max_rejected(self):
        """Test that Task rejects depth > MAX_TASK_DEPTH."""
        from datetime import datetime, timezone
        from asap.models.constants import MAX_TASK_DEPTH
        from asap.models.entities import Task

        with pytest.raises(ValidationError):
            Task(
                id=generate_id(),
                conversation_id=generate_id(),
                status="submitted",
                depth=MAX_TASK_DEPTH + 1,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

    def test_task_timestamps(self):
        """Test that Task timestamps are properly set."""
        from datetime import datetime, timezone
        from asap.models.entities import Task

        created = datetime.now(timezone.utc)
        task = Task(
            id=generate_id(),
            conversation_id=generate_id(),
            status="submitted",
            created_at=created,
            updated_at=created,
        )

        assert task.created_at == created
        assert task.updated_at == created
        assert task.created_at.tzinfo == timezone.utc

    def test_task_is_terminal_delegates_to_status(self) -> None:
        """Task.is_terminal() mirrors TaskStatus.is_terminal()."""
        from datetime import datetime, timezone

        from asap.models.entities import Task
        from asap.models.enums import TaskStatus

        now = datetime.now(timezone.utc)
        for status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ):
            task = Task(
                id=generate_id(),
                conversation_id=generate_id(),
                status=status,
                created_at=now,
                updated_at=now,
            )
            assert task.is_terminal()

        for status in (
            TaskStatus.SUBMITTED,
            TaskStatus.WORKING,
            TaskStatus.INPUT_REQUIRED,
        ):
            task = Task(
                id=generate_id(),
                conversation_id=generate_id(),
                status=status,
                created_at=now,
                updated_at=now,
            )
            assert not task.is_terminal()

    def test_task_json_schema(self):
        """Test that Task generates valid JSON Schema."""
        from asap.models.entities import Task

        schema = Task.model_json_schema()

        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "conversation_id" in schema["properties"]
        assert "status" in schema["properties"]
        assert "depth" in schema["properties"]
        assert "progress" in schema["properties"]
        assert "created_at" in schema["properties"]
        assert "updated_at" in schema["properties"]

        required = set(schema["required"])
        assert "id" in required
        assert "conversation_id" in required
        assert "status" in required


class TestMessage:
    """Test suite for Message entity model."""

    def test_message_creation_minimal(self):
        """Test creating a Message with minimal required fields."""
        from datetime import datetime, timezone
        from asap.models.entities import Message

        msg_id = generate_id()
        task_id = generate_id()
        timestamp = datetime.now(timezone.utc)

        message = Message(
            id=msg_id,
            task_id=task_id,
            sender="urn:asap:agent:coordinator",
            role="user",
            parts=[],
            timestamp=timestamp,
        )

        assert message.id == msg_id
        assert message.task_id == task_id
        assert message.sender == "urn:asap:agent:coordinator"
        assert message.role == "user"
        assert len(message.parts) == 0

    def test_message_with_parts_reference(self):
        """Test creating a Message with parts reference."""
        from datetime import datetime, timezone
        from asap.models.entities import Message

        # Parts will be a list of part IDs (references)
        message = Message(
            id=generate_id(),
            task_id=generate_id(),
            sender="urn:asap:agent:test",
            role="assistant",
            parts=["part_01HX5K...", "part_01HX5L..."],
            timestamp=datetime.now(timezone.utc),
        )

        assert len(message.parts) == 2
        assert message.role == "assistant"

    def test_message_roles(self):
        """Test different message roles."""
        from datetime import datetime, timezone
        from asap.models.entities import Message

        for role in ["user", "assistant", "system"]:
            message = Message(
                id=generate_id(),
                task_id=generate_id(),
                sender="urn:asap:agent:test",
                role=role,
                parts=[],
                timestamp=datetime.now(timezone.utc),
            )
            assert message.role == role

    def test_message_timestamp(self):
        """Test that Message timestamp is timezone-aware."""
        from datetime import datetime, timezone
        from asap.models.entities import Message

        timestamp = datetime.now(timezone.utc)
        message = Message(
            id=generate_id(),
            task_id=generate_id(),
            sender="urn:asap:agent:test",
            role="user",
            parts=[],
            timestamp=timestamp,
        )

        assert message.timestamp == timestamp
        assert message.timestamp.tzinfo == timezone.utc

    def test_message_json_schema(self):
        """Test that Message generates valid JSON Schema."""
        from asap.models.entities import Message

        schema = Message.model_json_schema()

        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "task_id" in schema["properties"]
        assert "sender" in schema["properties"]
        assert "role" in schema["properties"]
        assert "parts" in schema["properties"]
        assert "timestamp" in schema["properties"]

        required = set(schema["required"])
        assert "id" in required
        assert "task_id" in required
        assert "sender" in required
        assert "role" in required
        assert "parts" in required
        assert "timestamp" in required


class TestArtifact:
    """Test suite for Artifact entity model."""

    def test_artifact_creation_minimal(self):
        """Test creating an Artifact with minimal required fields."""
        from datetime import datetime, timezone
        from asap.models.entities import Artifact

        artifact_id = generate_id()
        task_id = generate_id()
        created_at = datetime.now(timezone.utc)

        artifact = Artifact(
            id=artifact_id,
            task_id=task_id,
            name="Q3 Market Analysis Report",
            parts=["part_01HX5K..."],
            created_at=created_at,
        )

        assert artifact.id == artifact_id
        assert artifact.task_id == task_id
        assert artifact.name == "Q3 Market Analysis Report"
        assert len(artifact.parts) == 1

    def test_artifact_with_multiple_parts(self):
        """Test creating an Artifact with multiple parts."""
        from datetime import datetime, timezone
        from asap.models.entities import Artifact

        artifact = Artifact(
            id=generate_id(),
            task_id=generate_id(),
            name="Research Report",
            parts=["part_01HX5K...", "part_01HX5L...", "part_01HX5M..."],
            created_at=datetime.now(timezone.utc),
        )

        assert len(artifact.parts) == 3
        assert artifact.name == "Research Report"

    def test_artifact_timestamp(self):
        """Test that Artifact timestamp is timezone-aware."""
        from datetime import datetime, timezone
        from asap.models.entities import Artifact

        created = datetime.now(timezone.utc)
        artifact = Artifact(
            id=generate_id(),
            task_id=generate_id(),
            name="Test Artifact",
            parts=[],
            created_at=created,
        )

        assert artifact.created_at == created
        assert artifact.created_at.tzinfo == timezone.utc

    def test_artifact_json_schema(self):
        """Test that Artifact generates valid JSON Schema."""
        from asap.models.entities import Artifact

        schema = Artifact.model_json_schema()

        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "task_id" in schema["properties"]
        assert "name" in schema["properties"]
        assert "parts" in schema["properties"]
        assert "created_at" in schema["properties"]

        required = set(schema["required"])
        assert "id" in required
        assert "task_id" in required
        assert "name" in required
        assert "parts" in required
        assert "created_at" in required


class TestStateSnapshot:
    """Test suite for StateSnapshot entity model."""

    def test_state_snapshot_creation_minimal(self):
        """Test creating a StateSnapshot with minimal required fields."""
        from datetime import datetime, timezone
        from asap.models.entities import StateSnapshot

        snapshot_id = generate_id()
        task_id = generate_id()
        created_at = datetime.now(timezone.utc)

        snapshot = StateSnapshot(
            id=snapshot_id,
            task_id=task_id,
            version=1,
            data={"search_completed": True, "sources_analyzed": 15},
            created_at=created_at,
        )

        assert snapshot.id == snapshot_id
        assert snapshot.task_id == task_id
        assert snapshot.version == 1
        assert snapshot.data["search_completed"] is True
        assert snapshot.checkpoint is False  # Default

    def test_state_snapshot_with_checkpoint(self):
        """Test creating a StateSnapshot with checkpoint flag."""
        from datetime import datetime, timezone
        from asap.models.entities import StateSnapshot

        snapshot = StateSnapshot(
            id=generate_id(),
            task_id=generate_id(),
            version=3,
            data={"key_findings": ["finding1", "finding2"]},
            checkpoint=True,
            created_at=datetime.now(timezone.utc),
        )

        assert snapshot.version == 3
        assert snapshot.checkpoint is True
        assert len(snapshot.data["key_findings"]) == 2

    def test_state_snapshot_version_increment(self):
        """Test that StateSnapshot versions can be incremented."""
        from datetime import datetime, timezone
        from asap.models.entities import StateSnapshot

        task_id = generate_id()
        created = datetime.now(timezone.utc)

        # Create snapshots with incrementing versions
        snapshot1 = StateSnapshot(
            id=generate_id(), task_id=task_id, version=1, data={"step": 1}, created_at=created
        )

        snapshot2 = StateSnapshot(
            id=generate_id(), task_id=task_id, version=2, data={"step": 2}, created_at=created
        )

        snapshot3 = StateSnapshot(
            id=generate_id(), task_id=task_id, version=3, data={"step": 3}, created_at=created
        )

        assert snapshot1.version == 1
        assert snapshot2.version == 2
        assert snapshot3.version == 3
        assert snapshot1.task_id == snapshot2.task_id == snapshot3.task_id

    def test_state_snapshot_complex_data(self):
        """Test StateSnapshot with complex nested data."""
        from datetime import datetime, timezone
        from asap.models.entities import StateSnapshot

        complex_data = {
            "search_completed": True,
            "sources_analyzed": 15,
            "key_findings": [
                {"title": "Finding 1", "confidence": 0.95},
                {"title": "Finding 2", "confidence": 0.87},
            ],
            "metadata": {"total_time_ms": 45000, "tokens_used": 12500},
        }

        snapshot = StateSnapshot(
            id=generate_id(),
            task_id=generate_id(),
            version=1,
            data=complex_data,
            created_at=datetime.now(timezone.utc),
        )

        assert snapshot.data["sources_analyzed"] == 15
        assert len(snapshot.data["key_findings"]) == 2
        assert snapshot.data["metadata"]["tokens_used"] == 12500

    def test_state_snapshot_timestamp(self):
        """Test that StateSnapshot timestamp is timezone-aware."""
        from datetime import datetime, timezone
        from asap.models.entities import StateSnapshot

        created = datetime.now(timezone.utc)
        snapshot = StateSnapshot(
            id=generate_id(), task_id=generate_id(), version=1, data={}, created_at=created
        )

        assert snapshot.created_at == created
        assert snapshot.created_at.tzinfo == timezone.utc

    def test_state_snapshot_json_schema(self):
        """Test that StateSnapshot generates valid JSON Schema."""
        from asap.models.entities import StateSnapshot

        schema = StateSnapshot.model_json_schema()

        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "task_id" in schema["properties"]
        assert "version" in schema["properties"]
        assert "data" in schema["properties"]
        assert "checkpoint" in schema["properties"]
        assert "created_at" in schema["properties"]

        required = set(schema["required"])
        assert "id" in required
        assert "task_id" in required
        assert "version" in required
        assert "data" in required
        assert "created_at" in required

    def test_manifest_agent_id_urn_validation_failure(self) -> None:
        """Test that invalid agent URNs in manifest raise validation errors."""
        from asap.models.entities import Capability, Endpoint

        with pytest.raises(ValidationError) as exc_info:
            Manifest(
                id="invalid-urn",  # Should be urn:asap:agent:name format
                name="test-manifest",
                version="1.0.0",
                description="Test manifest",
                capabilities=Capability(asap_version="0.1", skills=[]),
                endpoints=Endpoint(asap="https://example.com"),
            )

        error_detail = exc_info.value.errors()[0]
        assert "must follow URN format" in error_detail["msg"]

    def test_manifest_version_validation_failure(self) -> None:
        """Test that invalid semantic versions raise validation errors."""
        from asap.models.entities import Capability, Endpoint

        with pytest.raises(ValidationError) as exc_info:
            Manifest(
                id="urn:asap:agent:test",
                name="test-manifest",
                version="invalid.version",  # Invalid semver
                description="Test manifest",
                capabilities=Capability(asap_version="0.1", skills=[]),
                endpoints=Endpoint(asap="https://example.com"),
            )

        error_detail = exc_info.value.errors()[0]
        assert "Invalid semantic version" in error_detail["msg"]

    def test_task_can_be_cancelled_method(self) -> None:
        """Test the can_be_cancelled method on Task."""
        from asap.models.entities import Task
        from asap.models.enums import TaskStatus
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        # Test SUBMITTED task can be cancelled
        submitted_task = Task(
            id="task_01HX5K4N000000000000000000",
            conversation_id="conv_01HX5K3MQVN8000000000000000",
            status=TaskStatus.SUBMITTED,
            created_at=now,
            updated_at=now,
        )
        assert submitted_task.can_be_cancelled()

        # Test WORKING task can be cancelled
        working_task = Task(
            id="task_01HX5K4N000000000000000001",
            conversation_id="conv_01HX5K3MQVN8000000000000000",
            status=TaskStatus.WORKING,
            created_at=now,
            updated_at=now,
        )
        assert working_task.can_be_cancelled()

        # Test COMPLETED task cannot be cancelled
        completed_task = Task(
            id="task_01HX5K4N000000000000000002",
            conversation_id="conv_01HX5K3MQVN8000000000000000",
            status=TaskStatus.COMPLETED,
            created_at=now,
            updated_at=now,
        )
        assert not completed_task.can_be_cancelled()


class TestManifestAuthSchemeValidation:
    """Test suite for Manifest authentication scheme validation."""

    def test_manifest_with_valid_bearer_scheme(self) -> None:
        """Test that Manifest with bearer scheme is accepted."""
        manifest = Manifest(
            id="urn:asap:agent:test-v1",
            name="Test Agent",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://api.example.com/asap"),
            auth=AuthScheme(schemes=["bearer"]),
        )

        assert manifest.auth is not None
        assert manifest.auth.schemes == ["bearer"]

    def test_manifest_with_valid_basic_scheme(self) -> None:
        """Test that Manifest with basic scheme is accepted."""
        manifest = Manifest(
            id="urn:asap:agent:test-v1",
            name="Test Agent",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://api.example.com/asap"),
            auth=AuthScheme(schemes=["basic"]),
        )

        assert manifest.auth is not None
        assert manifest.auth.schemes == ["basic"]

    def test_manifest_with_multiple_valid_schemes(self) -> None:
        """Test that Manifest with multiple valid schemes is accepted."""
        manifest = Manifest(
            id="urn:asap:agent:test-v1",
            name="Test Agent",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://api.example.com/asap"),
            auth=AuthScheme(schemes=["bearer", "basic"]),
        )

        assert manifest.auth is not None
        assert set(manifest.auth.schemes) == {"bearer", "basic"}

    def test_manifest_with_unsupported_scheme_raises_error(self) -> None:
        """Test that Manifest with unsupported scheme raises UnsupportedAuthSchemeError."""
        from asap.errors import UnsupportedAuthSchemeError

        with pytest.raises(UnsupportedAuthSchemeError) as exc_info:
            Manifest(
                id="urn:asap:agent:test-v1",
                name="Test Agent",
                version="1.0.0",
                description="A test agent",
                capabilities=Capability(asap_version="0.1", skills=[]),
                endpoints=Endpoint(asap="https://api.example.com/asap"),
                auth=AuthScheme(schemes=["oauth2"]),  # oauth2 not yet supported
            )

        assert exc_info.value.scheme == "oauth2"
        assert "oauth2" in exc_info.value.message.lower()
        assert (
            "bearer" in exc_info.value.message.lower() or "basic" in exc_info.value.message.lower()
        )

    def test_manifest_with_mixed_valid_and_invalid_schemes_raises_error(self) -> None:
        """Test that Manifest with mix of valid and invalid schemes raises error."""
        from asap.errors import UnsupportedAuthSchemeError

        with pytest.raises(UnsupportedAuthSchemeError) as exc_info:
            Manifest(
                id="urn:asap:agent:test-v1",
                name="Test Agent",
                version="1.0.0",
                description="A test agent",
                capabilities=Capability(asap_version="0.1", skills=[]),
                endpoints=Endpoint(asap="https://api.example.com/asap"),
                auth=AuthScheme(schemes=["bearer", "hmac"]),  # hmac not yet supported
            )

        assert exc_info.value.scheme == "hmac"

    def test_manifest_with_empty_schemes_list(self) -> None:
        """Test that Manifest with empty schemes list is handled.

        Note: Pydantic doesn't automatically validate non-empty lists.
        An empty schemes list will pass validation but our validator
        will iterate over an empty list (which is fine - no error raised).
        """
        # Empty schemes list is technically valid (no schemes to validate)
        # Our validator iterates over schemes, so empty list = no validation needed
        manifest = Manifest(
            id="urn:asap:agent:test-v1",
            name="Test Agent",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://api.example.com/asap"),
            auth=AuthScheme(schemes=[]),  # Empty list - validator handles gracefully
        )

        assert manifest.auth is not None
        assert manifest.auth.schemes == []

    def test_manifest_without_auth_works(self) -> None:
        """Test that Manifest without auth field works (auth is optional)."""
        manifest = Manifest(
            id="urn:asap:agent:test-v1",
            name="Test Agent",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://api.example.com/asap"),
            # auth is None by default
        )

        assert manifest.auth is None

    def test_manifest_auth_scheme_case_insensitive(self) -> None:
        """Test that auth scheme validation is case-insensitive."""
        # Should accept "Bearer" (capitalized) as valid
        manifest = Manifest(
            id="urn:asap:agent:test-v1",
            name="Test Agent",
            version="1.0.0",
            description="A test agent",
            capabilities=Capability(asap_version="0.1", skills=[]),
            endpoints=Endpoint(asap="https://api.example.com/asap"),
            auth=AuthScheme(schemes=["Bearer"]),  # Capitalized
        )

        assert manifest.auth is not None
        assert manifest.auth.schemes == ["Bearer"]  # Original case preserved
