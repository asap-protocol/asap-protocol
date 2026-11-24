"""The README smoke command of ``examples/a2a_gateway/``.

``uv run python -m examples.a2a_gateway.smoke`` runs the flow in one
process: the a2a-sdk client verifies the card, ASAP's ingest verifies it and
maps it to the manifest, and one skill call succeeds with a local-issuer token.
"""

from __future__ import annotations

from pytest import CaptureFixture

from examples.a2a_gateway import smoke


def test_the_smoke_run_prints_each_step_of_exit_flow_a(capsys: CaptureFixture[str]) -> None:
    smoke.main()

    assert capsys.readouterr().out.splitlines() == [
        "a2a-sdk client: card 'A2A Gateway Demo' verified (EdDSA, kid gateway-card-1)",
        "ASAP ingest: card verified, manifest urn:asap:agent:a2a-gateway-demo",
        "greet with scope demo:greet: {'greeting': 'Hello, Ada!'}",
    ]
