"""FilePart MIME checks accept any letter case and keep the given spelling."""

from __future__ import annotations

from asap.models.parts import FilePart


def test_uppercase_mime_type_is_accepted_unchanged() -> None:
    """``APPLICATION/PDF`` passes the type/subtype check and is stored as given."""
    part = FilePart(
        type="file",
        uri="https://cdn.example/report.pdf",
        mime_type="APPLICATION/PDF",
    )
    assert part.mime_type == "APPLICATION/PDF"
