"""Registry categories drop surrounding whitespace before name matching."""

from __future__ import annotations

from asap.discovery.registry import RegistryEntry


def _entry(category: str) -> RegistryEntry:
    """Build a minimal registry row whose only varied field is ``category``."""
    return RegistryEntry.model_validate(
        {
            "id": "urn:asap:agent:test:bot",
            "name": "Test",
            "description": "x",
            "endpoints": {"http": "https://example.com"},
            "asap_version": "2.0",
            "category": category,
        }
    )


def test_padded_category_strips_before_canonical_match() -> None:
    """Padding around a known category becomes the canonical label.

    Padding around an unknown category is removed and the label is kept.
    """
    assert _entry("  coding  ").category == "Coding"
    assert _entry("\tResearch\n").category == "Research"
    assert _entry("  Robotics  ").category == "Robotics"
