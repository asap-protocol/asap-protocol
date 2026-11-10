"""Tests for Part models (content types for messages and artifacts)."""

import pytest
from pydantic import ValidationError


class TestTextPart:
    """Test suite for TextPart model."""

    def test_text_part_creation(self):
        """Test creating a TextPart with content."""
        from asap.models.parts import TextPart

        part = TextPart(type="text", content="Research Q3 market trends for AI infrastructure")

        assert part.type == "text"
        assert part.content == "Research Q3 market trends for AI infrastructure"
        assert "AI infrastructure" in part.content

    def test_text_part_empty_content(self):
        """Test that TextPart allows empty content."""
        from asap.models.parts import TextPart

        part = TextPart(type="text", content="")
        assert part.content == ""

    def test_text_part_multiline_content(self):
        """Test TextPart with multiline content."""
        from asap.models.parts import TextPart

        content = """Line 1
Line 2
Line 3"""
        part = TextPart(type="text", content=content)
        assert part.content == content
        assert "\n" in part.content

    def test_text_part_json_schema(self):
        """Test that TextPart generates valid JSON Schema."""
        from asap.models.parts import TextPart

        schema = TextPart.model_json_schema()

        assert schema["type"] == "object"
        assert "type" in schema["properties"]
        assert "content" in schema["properties"]
        assert set(schema["required"]) == {"type", "content"}


class TestDataPart:
    """Test suite for DataPart model."""

    def test_data_part_creation(self):
        """Test creating a DataPart with structured data."""
        from asap.models.parts import DataPart

        data = {"query": "AI trends", "max_results": 10}
        part = DataPart(type="data", data=data)

        assert part.type == "data"
        assert part.data == data
        assert part.data["query"] == "AI trends"
        assert part.schema_uri is None

    def test_data_part_with_schema_uri(self):
        """Test DataPart with optional schema_uri."""
        from asap.models.parts import DataPart

        part = DataPart(
            type="data", data={"value": 42}, schema_uri="https://example.com/schemas/number.json"
        )

        assert part.schema_uri == "https://example.com/schemas/number.json"
        assert part.data["value"] == 42

    def test_data_part_complex_data(self):
        """Test DataPart with complex nested data."""
        from asap.models.parts import DataPart

        complex_data = {
            "results": [{"title": "Result 1", "score": 0.95}, {"title": "Result 2", "score": 0.87}],
            "metadata": {"total": 2, "query_time_ms": 150},
        }

        part = DataPart(type="data", data=complex_data)
        assert len(part.data["results"]) == 2
        assert part.data["metadata"]["total"] == 2

    def test_data_part_json_schema(self):
        """Test that DataPart generates valid JSON Schema."""
        from asap.models.parts import DataPart

        schema = DataPart.model_json_schema()

        assert schema["type"] == "object"
        assert "type" in schema["properties"]
        assert "data" in schema["properties"]
        assert "schema_uri" in schema["properties"]
        assert "type" in schema["required"]
        assert "data" in schema["required"]


class TestFilePart:
    """Test suite for FilePart model."""

    def test_file_part_creation(self):
        """Test creating a FilePart with URI and mime type."""
        from asap.models.parts import FilePart

        part = FilePart(
            type="file", uri="asap://artifacts/task_123/report.pdf", mime_type="application/pdf"
        )

        assert part.type == "file"
        assert part.uri == "asap://artifacts/task_123/report.pdf"
        assert part.mime_type == "application/pdf"
        assert part.inline_data is None

    def test_file_part_with_inline_data(self):
        """Test FilePart with optional inline_data."""
        from asap.models.parts import FilePart

        part = FilePart(
            type="file",
            uri="data:text/plain;base64,SGVsbG8gV29ybGQ=",
            mime_type="text/plain",
            inline_data="SGVsbG8gV29ybGQ=",
        )

        assert part.inline_data == "SGVsbG8gV29ybGQ="
        assert part.mime_type == "text/plain"

    def test_file_part_various_mime_types(self):
        """Test FilePart with different mime types."""
        from asap.models.parts import FilePart

        mime_types = ["application/pdf", "image/png", "text/csv", "application/json"]

        for mime_type in mime_types:
            part = FilePart(
                type="file",
                uri=f"https://example.com/example.{mime_type.split('/')[-1]}",
                mime_type=mime_type,
            )
            assert part.mime_type == mime_type

    def test_file_part_json_schema(self):
        """Test that FilePart generates valid JSON Schema."""
        from asap.models.parts import FilePart

        schema = FilePart.model_json_schema()

        assert schema["type"] == "object"
        assert "type" in schema["properties"]
        assert "uri" in schema["properties"]
        assert "mime_type" in schema["properties"]

    def test_file_part_invalid_mime_type(self) -> None:
        """Test that invalid MIME types raise validation errors."""
        from asap.models.parts import FilePart

        with pytest.raises(ValidationError) as exc_info:
            FilePart(
                type="file",
                uri="https://example.com/example.txt",
                mime_type="invalid-mime-type",  # Invalid format
            )

        error_detail = exc_info.value.errors()[0]
        assert "Invalid MIME type format" in error_detail["msg"]

        with pytest.raises(ValidationError) as exc_info:
            FilePart(
                type="file",
                uri="https://example.com/example.txt",
                mime_type="text",  # Missing subtype
            )

        error_detail = exc_info.value.errors()[0]
        assert "Invalid MIME type format" in error_detail["msg"]

        # Valid MIME types should work
        part = FilePart(
            type="file",
            uri="https://example.com/example.txt",
            mime_type="text/plain",
        )
        assert part.mime_type == "text/plain"

        part = FilePart(
            type="file",
            uri="https://example.com/example.json",
            mime_type="application/vnd.api+json",
        )
        assert part.mime_type == "application/vnd.api+json"

    def test_file_part_invalid_base64_inline_data_raises_error(self) -> None:
        """Test that invalid base64 inline_data raises validation error."""
        from asap.models.parts import FilePart

        with pytest.raises(ValidationError) as exc_info:
            FilePart(
                type="file",
                uri="https://example.com/example.txt",
                mime_type="text/plain",
                inline_data="!!!not-valid-base64!!!",
            )

        error_detail = exc_info.value.errors()[0]
        assert "inline_data must be valid base64" in error_detail["msg"]

    def test_file_part_invalid_base64_with_spaces_raises_error(self) -> None:
        """Test that base64 with invalid characters raises validation error."""
        from asap.models.parts import FilePart

        with pytest.raises(ValidationError) as exc_info:
            FilePart(
                type="file",
                uri="https://example.com/example.txt",
                mime_type="text/plain",
                inline_data="SGVsbG8g V29ybGQ=",  # Space in middle
            )

        error_detail = exc_info.value.errors()[0]
        assert "inline_data must be valid base64" in error_detail["msg"]

    def test_file_part_valid_base64_inline_data(self) -> None:
        """Test that valid base64 inline_data is accepted."""
        import base64

        from asap.models.parts import FilePart

        # "Hello World" encoded in base64
        valid_base64 = base64.b64encode(b"Hello World").decode("utf-8")

        part = FilePart(
            type="file",
            uri="https://example.com/example.txt",
            mime_type="text/plain",
            inline_data=valid_base64,
        )

        assert part.inline_data == valid_base64
        # Verify it decodes correctly
        decoded = base64.b64decode(part.inline_data)
        assert decoded == b"Hello World"

    def test_file_part_none_inline_data_is_valid(self) -> None:
        """Test that None inline_data is valid."""
        from asap.models.parts import FilePart

        part = FilePart(
            type="file",
            uri="https://example.com/example.txt",
            mime_type="text/plain",
            inline_data=None,
        )

        assert part.inline_data is None

    def test_file_part_uri_rejects_path_traversal(self) -> None:
        """Test that FilePart rejects URIs containing path traversal (..)."""
        from asap.models.parts import FilePart

        with pytest.raises(ValidationError) as exc_info:
            FilePart(
                type="file",
                uri="https://example.com/../../../etc/passwd",
                mime_type="text/plain",
            )
        error_detail = exc_info.value.errors()[0]
        assert "path traversal" in error_detail["msg"].lower() or ".." in error_detail["msg"]

        with pytest.raises(ValidationError) as exc_info:
            FilePart(
                type="file",
                uri="asap://artifacts/../secret",
                mime_type="application/pdf",
            )
        error_detail = exc_info.value.errors()[0]
        assert "path traversal" in error_detail["msg"].lower() or ".." in error_detail["msg"]

    def test_file_part_uri_rejects_file_scheme(self) -> None:
        """Test that FilePart rejects file:// URIs for security."""
        from asap.models.parts import FilePart

        with pytest.raises(ValidationError) as exc_info:
            FilePart(
                type="file",
                uri="file:///etc/passwd",
                mime_type="text/plain",
            )
        error_detail = exc_info.value.errors()[0]
        assert (
            "file://" in error_detail["msg"].lower() or "not allowed" in error_detail["msg"].lower()
        )

        with pytest.raises(ValidationError) as exc_info:
            FilePart(
                type="file",
                uri="FILE:///path/to/file",
                mime_type="text/plain",
            )
        error_detail = exc_info.value.errors()[0]
        assert (
            "file://" in error_detail["msg"].lower() or "not allowed" in error_detail["msg"].lower()
        )

    @pytest.mark.parametrize(
        "uri",
        [
            "  file:///etc/passwd",
            "\tfile:///etc/passwd",
            "  FILE:///etc/passwd",
        ],
    )
    def test_file_part_uri_rejects_padded_file_scheme(self, uri: str) -> None:
        """Leading whitespace must not bypass the local-file scheme check.

        ``strip`` and ``lower`` both run before the ``file://`` prefix test.
        ``  FILE:///etc/passwd`` is still rejected.

        Example:
            >>> FilePart(type="file", uri="  FILE:///etc/passwd", mime_type="text/plain")
        """
        from asap.models.parts import FilePart

        with pytest.raises(ValidationError) as exc_info:
            FilePart(type="file", uri=uri, mime_type="text/plain")
        assert "not allowed" in exc_info.value.errors()[0]["msg"].lower()

    def test_file_part_uri_accepts_valid_uris(self) -> None:
        """Test that FilePart accepts asap://, https://, and data: URIs."""
        from asap.models.parts import FilePart

        part_asap = FilePart(
            type="file",
            uri="asap://artifacts/task_123/report.pdf",
            mime_type="application/pdf",
        )
        assert part_asap.uri == "asap://artifacts/task_123/report.pdf"

        part_https = FilePart(
            type="file",
            uri="https://example.com/doc.pdf",
            mime_type="application/pdf",
        )
        assert part_https.uri == "https://example.com/doc.pdf"

        part_data = FilePart(
            type="file",
            uri="data:text/plain;base64,SGVsbG8=",
            mime_type="text/plain",
        )
        assert part_data.uri == "data:text/plain;base64,SGVsbG8="


class TestResourcePart:
    """Test suite for ResourcePart model."""

    def test_resource_part_creation(self):
        """Test creating a ResourcePart with resource URI."""
        from asap.models.parts import ResourcePart

        part = ResourcePart(
            type="resource", resource_uri="mcp://tools.example.com/web/search_results"
        )

        assert part.type == "resource"
        assert part.resource_uri == "mcp://tools.example.com/web/search_results"

    def test_resource_part_various_uris(self):
        """Test ResourcePart with different URI schemes."""
        from asap.models.parts import ResourcePart

        uris = ["mcp://server/resource", "asap://resource/123", "https://example.com/resource"]

        for uri in uris:
            part = ResourcePart(type="resource", resource_uri=uri)
            assert part.resource_uri == uri

    def test_resource_part_json_schema(self):
        """Test that ResourcePart generates valid JSON Schema."""
        from asap.models.parts import ResourcePart

        schema = ResourcePart.model_json_schema()

        assert schema["type"] == "object"
        assert "type" in schema["properties"]
        assert "resource_uri" in schema["properties"]
        assert set(schema["required"]) == {"type", "resource_uri"}


class TestTemplatePart:
    """Test suite for TemplatePart model."""

    def test_template_part_creation(self):
        """Test creating a TemplatePart with template and variables."""
        from asap.models.parts import TemplatePart

        part = TemplatePart(
            type="template",
            template="Research {{topic}} for {{timeframe}}",
            variables={"topic": "AI trends", "timeframe": "Q3 2025"},
        )

        assert part.type == "template"
        assert part.template == "Research {{topic}} for {{timeframe}}"
        assert part.variables["topic"] == "AI trends"
        assert part.variables["timeframe"] == "Q3 2025"

    def test_template_part_empty_variables(self):
        """Test TemplatePart with empty variables dict."""
        from asap.models.parts import TemplatePart

        part = TemplatePart(
            type="template", template="Static template with no variables", variables={}
        )

        assert part.variables == {}
        assert part.template == "Static template with no variables"

    def test_template_part_complex_variables(self):
        """Test TemplatePart with complex variable values."""
        from asap.models.parts import TemplatePart

        variables = {
            "items": ["item1", "item2", "item3"],
            "config": {"max_depth": 5, "timeout": 30},
            "count": 42,
        }

        part = TemplatePart(
            type="template", template="Process {{count}} items", variables=variables
        )

        assert part.variables["count"] == 42
        assert len(part.variables["items"]) == 3
        assert part.variables["config"]["max_depth"] == 5

    def test_template_part_json_schema(self):
        """Test that TemplatePart generates valid JSON Schema."""
        from asap.models.parts import TemplatePart

        schema = TemplatePart.model_json_schema()

        assert schema["type"] == "object"
        assert "type" in schema["properties"]
        assert "template" in schema["properties"]
        assert "variables" in schema["properties"]
        assert set(schema["required"]) == {"type", "template", "variables"}


class TestPartDiscriminatedUnion:
    """Test suite for Part discriminated union."""

    def test_part_union_text_deserialization(self):
        """Test deserializing TextPart through Part union."""
        from asap.models.parts import Part

        data = {"type": "text", "content": "Hello World"}
        part = Part.validate_python(data)

        assert part.type == "text"
        assert part.content == "Hello World"

    def test_part_union_data_deserialization(self):
        """Test deserializing DataPart through Part union."""
        from asap.models.parts import Part

        data = {"type": "data", "data": {"key": "value"}}
        part = Part.validate_python(data)

        assert part.type == "data"
        assert part.data["key"] == "value"

    def test_part_union_file_deserialization(self):
        """Test deserializing FilePart through Part union."""
        from asap.models.parts import Part

        data = {
            "type": "file",
            "uri": "https://example.com/test.pdf",
            "mime_type": "application/pdf",
        }
        part = Part.validate_python(data)

        assert part.type == "file"
        assert part.uri == "https://example.com/test.pdf"

    def test_part_union_resource_deserialization(self):
        """Test deserializing ResourcePart through Part union."""
        from asap.models.parts import Part

        data = {"type": "resource", "resource_uri": "mcp://resource"}
        part = Part.validate_python(data)

        assert part.type == "resource"
        assert part.resource_uri == "mcp://resource"

    def test_part_union_template_deserialization(self):
        """Test deserializing TemplatePart through Part union."""
        from asap.models.parts import Part

        data = {"type": "template", "template": "Hello {{name}}", "variables": {"name": "World"}}
        part = Part.validate_python(data)

        assert part.type == "template"
        assert part.template == "Hello {{name}}"

    def test_part_union_invalid_type(self):
        """Test that invalid type raises validation error."""
        from asap.models.parts import Part

        data = {"type": "invalid", "content": "test"}

        with pytest.raises(ValidationError):
            Part.validate_python(data)

    def test_part_union_serialization(self):
        """Test serializing different Part types."""
        from asap.models.parts import TextPart, DataPart

        text_part = TextPart(type="text", content="Test")
        data_part = DataPart(type="data", data={"test": True})

        text_json = text_part.model_dump()
        data_json = data_part.model_dump()

        assert text_json["type"] == "text"
        assert data_json["type"] == "data"
