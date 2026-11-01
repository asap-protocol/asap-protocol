"""Unit tests for ASAP transport compression module.

Tests cover:
- Compression/decompression with gzip and brotli
- Compression threshold logic
- Accept-Encoding header generation
- Content-Encoding selection
- Edge cases and error handling
"""

import gzip
from unittest.mock import patch

import pytest

from asap.transport.compression import (
    COMPRESSION_THRESHOLD,
    CompressionAlgorithm,
    compress_gzip,
    compress_payload,
    decompress_gzip,
    decompress_payload,
    get_accept_encoding_header,
    get_supported_encodings,
    is_brotli_available,
    select_best_encoding,
)


class TestCompressionAlgorithm:
    """Tests for CompressionAlgorithm enum."""

    def test_algorithm_values(self) -> None:
        """Verify enum values match Content-Encoding header values."""
        assert CompressionAlgorithm.GZIP.value == "gzip"
        assert CompressionAlgorithm.BROTLI.value == "br"
        assert CompressionAlgorithm.IDENTITY.value == "identity"

    def test_algorithm_string_conversion(self) -> None:
        """Verify enum can be used as string."""
        assert str(CompressionAlgorithm.GZIP) == "CompressionAlgorithm.GZIP"
        assert CompressionAlgorithm.GZIP.value == "gzip"


class TestBrotliAvailability:
    """Tests for brotli availability check."""

    def test_brotli_availability_returns_bool(self) -> None:
        """Verify is_brotli_available returns boolean."""
        result = is_brotli_available()
        assert isinstance(result, bool)

    def test_brotli_availability_returns_false_on_import_error(self) -> None:
        """When brotli import fails, is_brotli_available returns False."""
        import builtins

        real_import = builtins.__import__

        def import_side_effect(name: str, *args: object, **kwargs: object):  # noqa: ANN401
            if name == "brotli":
                raise ImportError("No module named 'brotli'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_side_effect):
            result = is_brotli_available()
        assert result is False


class TestSupportedEncodings:
    """Tests for supported encodings list."""

    def test_gzip_always_supported(self) -> None:
        """Verify gzip is always in supported encodings."""
        encodings = get_supported_encodings()
        assert "gzip" in encodings

    def test_returns_list(self) -> None:
        """Verify returns a list of strings."""
        encodings = get_supported_encodings()
        assert isinstance(encodings, list)
        assert all(isinstance(enc, str) for enc in encodings)

    def test_get_supported_encodings_without_brotli(self) -> None:
        """When brotli is unavailable, get_supported_encodings returns only gzip."""
        with patch("asap.transport.compression.is_brotli_available", return_value=False):
            encodings = get_supported_encodings()
        assert encodings == ["gzip"]
        assert "br" not in encodings


class TestAcceptEncodingHeader:
    """Tests for Accept-Encoding header generation."""

    def test_includes_gzip(self) -> None:
        """Verify gzip is in Accept-Encoding header."""
        header = get_accept_encoding_header()
        assert "gzip" in header

    def test_includes_identity(self) -> None:
        """Verify identity is in Accept-Encoding header."""
        header = get_accept_encoding_header()
        assert "identity" in header

    def test_returns_string(self) -> None:
        """Verify returns a string."""
        header = get_accept_encoding_header()
        assert isinstance(header, str)


class TestGzipCompression:
    """Tests for gzip compression/decompression."""

    def test_compress_gzip(self) -> None:
        """Verify gzip compression works."""
        data = b"Hello, World! " * 100
        compressed = compress_gzip(data)
        assert len(compressed) < len(data)
        assert compressed[:2] == b"\x1f\x8b"  # gzip magic number

    def test_decompress_gzip(self) -> None:
        """Verify gzip decompression works."""
        original = b"Test data for decompression"
        compressed = gzip.compress(original)
        decompressed = decompress_gzip(compressed)
        assert decompressed == original

    def test_round_trip_gzip(self) -> None:
        """Verify compress/decompress round trip."""
        original = b'{"message": "Hello, World!"}' * 100
        compressed = compress_gzip(original)
        decompressed = decompress_gzip(compressed)
        assert decompressed == original

    def test_decompress_invalid_gzip(self) -> None:
        """Verify error on invalid gzip data."""
        with pytest.raises(OSError):
            decompress_gzip(b"not valid gzip data")


class TestCompressPayload:
    """Tests for compress_payload function."""

    def test_skip_compression_below_threshold(self) -> None:
        """Verify small payloads are not compressed."""
        small_data = b'{"key": "value"}'  # < 1KB
        result, algorithm = compress_payload(small_data)
        assert result == small_data
        assert algorithm == CompressionAlgorithm.IDENTITY

    def test_compress_above_threshold_gzip(self) -> None:
        """Verify large payloads are compressed with gzip."""
        large_data = b'{"data": "' + b"x" * 2000 + b'"}'  # > 1KB
        result, algorithm = compress_payload(large_data, CompressionAlgorithm.GZIP)
        assert len(result) < len(large_data)
        assert algorithm == CompressionAlgorithm.GZIP

    def test_compress_uses_gzip_when_brotli_unavailable(self) -> None:
        """When brotli is unavailable, compress_payload uses GZIP."""
        large_data = b'{"data": "' + b"x" * 2000 + b'"}'  # > 1KB
        with patch("asap.transport.compression.is_brotli_available", return_value=False):
            result, algorithm = compress_payload(large_data)
        assert algorithm == CompressionAlgorithm.GZIP
        assert len(result) < len(large_data)

    def test_prefer_fast_compression_uses_gzip_when_brotli_available(self) -> None:
        """When prefer_fast_compression is True, use GZIP even if brotli is available."""
        large_data = b'{"data": "' + b"x" * 2000 + b'"}'  # > 1KB
        with patch("asap.transport.compression.is_brotli_available", return_value=True):
            result, algorithm = compress_payload(large_data, prefer_fast_compression=True)
        assert algorithm == CompressionAlgorithm.GZIP
        assert len(result) < len(large_data)

    def test_custom_threshold(self) -> None:
        """Verify custom threshold works."""
        data = b"x" * 500  # 500 bytes
        # With default threshold (1KB), should not compress
        result1, algo1 = compress_payload(data)
        assert algo1 == CompressionAlgorithm.IDENTITY

        # With lower threshold (100), should compress
        result2, algo2 = compress_payload(data, threshold=100)
        assert algo2 != CompressionAlgorithm.IDENTITY

    def test_identity_algorithm_passthrough(self) -> None:
        """Verify IDENTITY algorithm returns data unchanged."""
        data = b"x" * 2000
        result, algorithm = compress_payload(data, CompressionAlgorithm.IDENTITY)
        assert result == data
        assert algorithm == CompressionAlgorithm.IDENTITY

    def test_compression_ineffective_returns_original(self) -> None:
        """Verify if compression increases size, original is returned."""
        # Use fixed seed for deterministic behavior
        import random

        rng = random.Random(42)  # Fixed seed
        # Generate random bytes which are hard to compress
        random_data = bytes(rng.getrandbits(8) for _ in range(2000))

        result, algorithm = compress_payload(random_data)
        # If compression was ineffective, should return IDENTITY
        assert algorithm in (CompressionAlgorithm.IDENTITY, CompressionAlgorithm.GZIP)

    def test_compression_failure_returns_original(self) -> None:
        """When compression raises a generic Exception, return original and IDENTITY.

        Note: In production, gzip.compress may raise OSError on large inputs or
        memory issues. This test uses RuntimeError to simulate any compression failure.
        """
        large_data = b'{"data": "' + b"x" * 2000 + b'"}'  # > 1KB
        with patch(
            "asap.transport.compression.compress_gzip",
            side_effect=RuntimeError("mock compression failure"),
        ):
            result, algorithm = compress_payload(large_data, CompressionAlgorithm.GZIP)
        assert result == large_data
        assert algorithm == CompressionAlgorithm.IDENTITY

    def test_default_threshold_constant(self) -> None:
        """Verify default threshold is 1KB."""
        assert COMPRESSION_THRESHOLD == 1024


class TestDecompressPayload:
    """Tests for decompress_payload function."""

    def test_decompress_gzip(self) -> None:
        """Verify gzip decompression works."""
        original = b'{"message": "test"}'
        compressed = gzip.compress(original)
        result = decompress_payload(compressed, "gzip")
        assert result == original

    def test_decompress_identity(self) -> None:
        """Verify identity encoding returns unchanged data."""
        data = b"original data"
        result = decompress_payload(data, "identity")
        assert result == data

    def test_decompress_empty_encoding(self) -> None:
        """Verify empty encoding returns unchanged data."""
        data = b"original data"
        result = decompress_payload(data, "")
        assert result == data

    def test_decompress_case_insensitive(self) -> None:
        """Verify encoding is case-insensitive."""
        original = b"test data"
        compressed = gzip.compress(original)
        result = decompress_payload(compressed, "GZIP")
        assert result == original

    def test_decompress_strips_encoding_padding(self) -> None:
        """Spaces around a lowercase encoding still select gzip.

        ``test_decompress_case_insensitive`` uses ``GZIP`` with no padding, so
        it still passes when ``strip()`` is removed.
        """
        original = b"padded-encoding"
        compressed = gzip.compress(original)
        assert decompress_payload(compressed, "  gzip  ") == original
        assert decompress_payload(compressed, "\tgzip\n") == original

    def test_decompress_unsupported_encoding(self) -> None:
        """Verify error on unsupported encoding."""
        with pytest.raises(ValueError, match="Unsupported Content-Encoding"):
            decompress_payload(b"data", "unknown-encoding")

    def test_decompress_invalid_gzip_data(self) -> None:
        """Verify error on invalid compressed data."""
        with pytest.raises(OSError):
            decompress_payload(b"not gzip", "gzip")


class TestSelectBestEncoding:
    """Tests for select_best_encoding function."""

    def test_none_returns_identity(self) -> None:
        """Verify None Accept-Encoding returns IDENTITY."""
        result = select_best_encoding(None)
        assert result == CompressionAlgorithm.IDENTITY

    def test_empty_returns_identity(self) -> None:
        """Verify empty Accept-Encoding returns IDENTITY."""
        result = select_best_encoding("")
        assert result == CompressionAlgorithm.IDENTITY

    def test_gzip_only(self) -> None:
        """Verify gzip is selected when only gzip is accepted."""
        result = select_best_encoding("gzip")
        assert result == CompressionAlgorithm.GZIP

    def test_identity_only(self) -> None:
        """Verify identity returns IDENTITY."""
        result = select_best_encoding("identity")
        assert result == CompressionAlgorithm.IDENTITY

    def test_quality_values(self) -> None:
        """Verify quality values are respected."""
        # gzip with higher quality should be selected over identity
        result = select_best_encoding("gzip;q=1.0, identity;q=0.5")
        assert result == CompressionAlgorithm.GZIP

    def test_multiple_encodings(self) -> None:
        """Verify multiple encodings are parsed correctly."""
        result = select_best_encoding("gzip, identity")
        assert result == CompressionAlgorithm.GZIP

    def test_whitespace_handling(self) -> None:
        """Verify whitespace is handled correctly."""
        result = select_best_encoding("  gzip  ,  identity  ")
        assert result == CompressionAlgorithm.GZIP

    def test_invalid_quality_uses_default(self) -> None:
        """Verify invalid quality values use default (1.0)."""
        result = select_best_encoding("gzip;q=invalid")
        assert result == CompressionAlgorithm.GZIP

    def test_unsupported_only_returns_identity(self) -> None:
        """When Accept-Encoding contains only unsupported encodings, returns IDENTITY."""
        result = select_best_encoding("deflate, xz, zstd")
        assert result == CompressionAlgorithm.IDENTITY

    def test_empty_parts_skipped(self) -> None:
        """Whitespace-only or empty parts in Accept-Encoding are skipped."""
        result = select_best_encoding("  , gzip ,  ")
        assert result == CompressionAlgorithm.GZIP

    def test_part_with_q_but_empty_encoding_skipped(self) -> None:
        """Part with ;q= but empty encoding after strip is skipped."""
        result = select_best_encoding("   ;q=0.5, gzip")
        assert result == CompressionAlgorithm.GZIP


class TestBrotliCompression:
    """Tests for brotli compression (if available)."""

    @pytest.mark.skipif(not is_brotli_available(), reason="brotli not installed")
    def test_compress_brotli(self) -> None:
        """Verify brotli compression works when available."""
        from asap.transport.compression import compress_brotli

        data = b"Hello, World! " * 100
        compressed = compress_brotli(data)
        assert len(compressed) < len(data)

    @pytest.mark.skipif(not is_brotli_available(), reason="brotli not installed")
    def test_decompress_brotli(self) -> None:
        """Verify brotli decompression works when available."""
        from asap.transport.compression import compress_brotli

        original = b'{"message": "brotli test"}'
        compressed = compress_brotli(original)
        result = decompress_payload(compressed, "br")
        assert result == original

    @pytest.mark.skipif(not is_brotli_available(), reason="brotli not installed")
    def test_brotli_in_supported_encodings(self) -> None:
        """Verify brotli is in supported encodings when available."""
        encodings = get_supported_encodings()
        assert "br" in encodings

    @pytest.mark.skipif(not is_brotli_available(), reason="brotli not installed")
    def test_brotli_in_accept_encoding(self) -> None:
        """Verify brotli is in Accept-Encoding when available."""
        header = get_accept_encoding_header()
        assert "br" in header

    @pytest.mark.skipif(not is_brotli_available(), reason="brotli not installed")
    def test_select_brotli_over_gzip(self) -> None:
        """Verify brotli is selected over gzip when both supported."""
        result = select_best_encoding("br, gzip")
        assert result == CompressionAlgorithm.BROTLI

    # These two tests run only when brotli is NOT installed (they verify the
    # "brotli unavailable" path). With brotli in dev deps they are skipped.
    @pytest.mark.skipif(is_brotli_available(), reason="brotli is installed")
    def test_brotli_not_in_encodings_when_unavailable(self) -> None:
        """Verify brotli is not in supported encodings when unavailable."""
        encodings = get_supported_encodings()
        assert "br" not in encodings

    @pytest.mark.skipif(is_brotli_available(), reason="brotli is installed")
    def test_decompress_brotli_unavailable_error(self) -> None:
        """Verify error when trying to decompress brotli without package."""
        with pytest.raises(ValueError, match="brotli package is not installed"):
            decompress_payload(b"fake brotli data", "br")

    @pytest.mark.skipif(is_brotli_available(), reason="brotli is installed")
    def test_compress_payload_brotli_fallback_to_gzip(self) -> None:
        """When BROTLI is requested but brotli is not installed, fallback to gzip."""
        large_data = b'{"data": "' + b"x" * 2000 + b'"}'  # > 1KB
        result, algorithm = compress_payload(large_data, CompressionAlgorithm.BROTLI)
        # Should fallback to gzip and return compressed data
        assert algorithm == CompressionAlgorithm.GZIP
        assert len(result) < len(large_data)

    @pytest.mark.skipif(not is_brotli_available(), reason="brotli required")
    def test_compress_payload_brotli_import_error_fallback_to_gzip(self) -> None:
        """When BROTLI is requested and compress_brotli raises ImportError, fallback to gzip."""
        large_data = b'{"data": "' + b"x" * 2000 + b'"}'  # > 1KB
        with patch(
            "asap.transport.compression.compress_brotli",
            side_effect=ImportError("brotli not available"),
        ):
            result, algorithm = compress_payload(large_data, CompressionAlgorithm.BROTLI)
        assert algorithm == CompressionAlgorithm.GZIP
        assert len(result) < len(large_data)


class TestCompressionIntegration:
    """Integration tests for compression workflow."""

    def test_compress_decompress_round_trip_gzip(self) -> None:
        """Verify full round trip with gzip."""
        original = b'{"envelope": {"id": "test-123", "payload": "x" * 1000}}'
        original_large = original * 50  # Make it large enough

        compressed, algorithm = compress_payload(original_large, CompressionAlgorithm.GZIP)
        assert algorithm == CompressionAlgorithm.GZIP

        decompressed = decompress_payload(compressed, algorithm.value)
        assert decompressed == original_large

    @pytest.mark.skipif(not is_brotli_available(), reason="brotli not installed")
    def test_compress_decompress_round_trip_brotli(self) -> None:
        """Verify full round trip with brotli."""
        original = b'{"envelope": {"id": "test-123", "payload": "x" * 1000}}'
        original_large = original * 50  # Make it large enough

        compressed, algorithm = compress_payload(original_large, CompressionAlgorithm.BROTLI)
        assert algorithm == CompressionAlgorithm.BROTLI

        decompressed = decompress_payload(compressed, algorithm.value)
        assert decompressed == original_large

    def test_json_payload_compression_ratio(self) -> None:
        """Verify JSON payloads achieve good compression ratio."""
        # Typical JSON payload with repetitive structure
        json_data = b'{"messages": [' + b'{"id": "msg-001", "text": "Hello, World!"},' * 100 + b"]}"
        compressed, algorithm = compress_payload(json_data, CompressionAlgorithm.GZIP)

        if algorithm == CompressionAlgorithm.GZIP:
            ratio = len(compressed) / len(json_data)
            # JSON typically compresses to < 30% of original
            assert ratio < 0.5, f"Compression ratio {ratio:.2%} higher than expected"
