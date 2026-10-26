"""Tests for scripts/telemetry/collect_pypi.py."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts.telemetry.collect_pypi import (
    DEFAULT_PYPI_PACKAGES,
    collect_pypi_recent,
    fetch_pypi_recent,
    normalize_recent_counts,
)

REQUIRED_DEFAULT_PYPI_PACKAGES: frozenset[str] = frozenset(
    {
        "asap-protocol",
        "asap-compliance",
    }
)


class TestDefaultPypiPackages:
    def test_defaults_cover_protocol_and_compliance(self) -> None:
        assert len(DEFAULT_PYPI_PACKAGES) >= 2
        assert REQUIRED_DEFAULT_PYPI_PACKAGES.issubset(DEFAULT_PYPI_PACKAGES)


class TestNormalizeRecentCounts:
    def test_parses_ints(self) -> None:
        data = {"last_day": 1, "last_week": 10, "last_month": 100}
        assert normalize_recent_counts(data) == data

    def test_parses_numeric_strings(self) -> None:
        data = {"last_day": "5", "last_week": "50", "last_month": "500"}
        assert normalize_recent_counts(data) == {
            "last_day": 5,
            "last_week": 50,
            "last_month": 500,
        }

    def test_rejects_missing_key(self) -> None:
        with pytest.raises(ValueError, match="last_week"):
            normalize_recent_counts({"last_day": 1, "last_month": 2})

    def test_rejects_non_object(self) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            normalize_recent_counts([])

    def test_rejects_bool_counts(self) -> None:
        """``bool`` is an ``int`` subclass, so the explicit bool check must stay.

        Example:
            >>> normalize_recent_counts({"last_day": True, ...})  # raises ValueError
        """
        payload_true = {"last_day": True, "last_week": 1, "last_month": 1}
        payload_false = {"last_day": False, "last_week": 1, "last_month": 1}
        with pytest.raises(ValueError, match="must be numeric"):
            normalize_recent_counts(payload_true)
        with pytest.raises(ValueError, match="must be numeric"):
            normalize_recent_counts(payload_false)

    def test_parses_grouped_and_padded_integer_strings(self) -> None:
        """Commas and surrounding space are presentation, not a different count.

        Example:
            >>> normalize_recent_counts({"last_day": "1,234", ...})["last_day"]
            1234
        """
        data = {"last_day": "1,234", "last_week": " 10 ", "last_month": "0"}
        assert normalize_recent_counts(data) == {
            "last_day": 1234,
            "last_week": 10,
            "last_month": 0,
        }

    def test_rejects_decimal_string(self) -> None:
        """A fractional string is not an integer download count.

        Example:
            >>> normalize_recent_counts({"last_day": "1.5", ...})  # raises ValueError
        """
        data = {"last_day": "1.5", "last_week": 1, "last_month": 1}
        with pytest.raises(ValueError, match="integer string"):
            normalize_recent_counts(data)


class TestFetchPypiRecent:
    @patch("scripts.telemetry.collect_pypi.pypistats.recent")
    def test_parses_json_envelope(
        self,
        mock_recent: object,
    ) -> None:
        mock_recent.return_value = json.dumps(
            {
                "package": "asap-protocol",
                "data": {"last_day": 2, "last_week": 20, "last_month": 200},
            }
        )
        out = fetch_pypi_recent("asap-protocol")
        assert out["package"] == "asap-protocol"
        assert out["downloads"] == {
            "last_day": 2,
            "last_week": 20,
            "last_month": 200,
        }

    @patch("scripts.telemetry.collect_pypi.pypistats.recent")
    def test_raises_on_no_data_message(self, mock_recent: object) -> None:
        mock_recent.return_value = "No data found for https://pypi.org/project/x/"
        with pytest.raises(ValueError, match="pypistats API"):
            fetch_pypi_recent("x")

    @patch("scripts.telemetry.collect_pypi.pypistats.recent")
    def test_raises_on_package_mismatch(self, mock_recent: object) -> None:
        mock_recent.return_value = json.dumps(
            {"package": "other", "data": {"last_day": 1, "last_week": 2, "last_month": 3}}
        )
        with pytest.raises(ValueError, match="mismatch"):
            fetch_pypi_recent("asap-protocol")


class TestCollectPypiRecent:
    @patch("scripts.telemetry.collect_pypi.pypistats.recent")
    def test_collects_multiple_packages(self, mock_recent: object) -> None:
        def _side_effect(pkg: str, **_kwargs: object) -> str:
            if pkg == "asap-protocol":
                counts = {"last_day": 1, "last_week": 10, "last_month": 100}
            else:
                counts = {"last_day": 0, "last_week": 3, "last_month": 30}
            return json.dumps({"package": pkg, "data": counts})

        mock_recent.side_effect = _side_effect

        report = collect_pypi_recent(("asap-protocol", "asap-cli"))
        assert report["source"] == "pypistats_recent"
        assert "collected_at" in report
        pkgs = report["packages"]
        assert pkgs["asap-protocol"]["downloads"]["last_week"] == 10
        assert pkgs["asap-cli"]["downloads"]["last_week"] == 3
        assert mock_recent.call_count == 2
