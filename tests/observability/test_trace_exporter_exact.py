"""Lock ``OTEL_TRACES_EXPORTER`` to none, otlp, and console.

The value is stripped and lowercased, then compared with ``==``.
``otlpx`` and ``consoleextra`` reject a superstring check (``"otlp" in name``).
``otl`` and ``consol`` reject a substring check (``name in "otlp"``).
``nonex`` and ``non`` install no exporter either. ``none`` itself installs
nothing, so those two stay empty under a containment check of ``none``;
``otlp`` and ``console`` are the branches a containment edit can widen.
`` none `` and ``OTLP`` stay on the allow-list because of that normalize step.
`` console `` is the strip lock: ``none`` with padding has the same empty
processor list as an unknown name.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import ConsoleSpanExporter

from asap.observability import tracing as tracing_module
from asap.observability.tracing import configure_tracing, reset_tracing

# Endpoint is set for every probe. A mistaken otlp branch then installs a
# processor instead of returning early for a missing endpoint.
_OTLP_ENDPOINT = "http://localhost:4317"

# Superstrings, then substrings. See the module docstring for which edit each
# group catches.
_EXPORTER_LOOKALIKES = (
    "nonex",
    "otlpx",
    "consoleextra",
    "non",
    "otl",
    "consol",
)


@pytest.fixture(autouse=True)
def _isolate_trace_exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset the process-global tracer provider around each exporter probe."""
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", _OTLP_ENDPOINT)
    reset_tracing()
    yield
    provider = tracing_module._tracer_provider
    if provider is not None:
        provider.shutdown()
    reset_tracing()


def _installed_exporter_types() -> tuple[type[object], ...]:
    """Return the exporter classes attached to the module tracer provider."""
    provider = tracing_module._tracer_provider
    assert provider is not None
    processors = provider._active_span_processor._span_processors
    return tuple(type(processor.span_exporter) for processor in processors)


@pytest.mark.parametrize("exporter_name", _EXPORTER_LOOKALIKES)
def test_trace_exporter_lookalikes_install_no_processor(
    monkeypatch: pytest.MonkeyPatch,
    exporter_name: str,
) -> None:
    """Names that only contain none, otlp, or console do not export spans."""
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", exporter_name)
    configure_tracing(service_name="trace-exporter-exact")
    assert _installed_exporter_types() == ()


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        (" none ", ()),
        ("OTLP", (OTLPSpanExporter,)),
        (" console ", (ConsoleSpanExporter,)),
    ],
)
def test_trace_exporter_accepts_normalized_allow_list(
    monkeypatch: pytest.MonkeyPatch,
    raw_name: str,
    expected: tuple[type[object], ...],
) -> None:
    """Padding and case still select none, otlp, or console."""
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", raw_name)
    configure_tracing(service_name="trace-exporter-exact")
    assert _installed_exporter_types() == expected
