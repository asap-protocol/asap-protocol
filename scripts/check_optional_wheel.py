"""Check one optional integration in a clean wheel installation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from importlib.metadata import distribution, version
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def main() -> None:
    """Check the extra metadata and call the installed tool without network access."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extra", choices=("crewai", "llamaindex"))
    extra = parser.parse_args().extra

    attempts: list[str] = []

    def block_network(event: str, _args: tuple[object, ...]) -> None:
        if event in {"socket.connect", "socket.sendto", "socket.getaddrinfo"}:
            attempts.append(event)
            raise AssertionError("The wheel test must run without network access.")

    sys.addaudithook(block_network)

    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    from asap.client.market import MarketClient
    from asap.models.entities import Capability, Endpoint, Manifest, Skill

    metadata = distribution("asap-protocol").metadata
    framework = "crewai" if extra == "crewai" else "llama-index-core"
    assert extra in (metadata.get_all("Provides-Extra") or []), f"Missing extra {extra}."
    requirements = map(Requirement, metadata.get_all("Requires-Dist") or [])
    assert any(
        canonicalize_name(requirement.name) == framework
        and requirement.marker is not None
        and requirement.marker.evaluate({"extra": extra})
        and not requirement.marker.evaluate({"extra": ""})
        for requirement in requirements
    ), f"Missing extra requirement for {framework}."

    urn = "urn:asap:agent:wheel-test"
    manifest = Manifest(
        id=urn,
        name="Wheel test",
        version="1.0.0",
        description="Test the optional tool.",
        capabilities=Capability(skills=[Skill(id="echo", description="Echo the message.")]),
        endpoints=Endpoint(asap="https://example.invalid/asap"),
    )
    expected = {"message": "hello"}
    agent = SimpleNamespace(manifest=manifest, run=AsyncMock(return_value=expected))
    client = MarketClient()
    with patch.object(client, "resolve", AsyncMock(return_value=agent)) as resolve:
        if extra == "crewai":
            from crewai.tools import BaseTool

            from asap.integrations.crewai import CrewAIAsapTool

            crew_tool = CrewAIAsapTool(urn, client=client)
            assert isinstance(crew_tool, BaseTool)
            output = crew_tool.run(input=expected)
        else:
            from llama_index.core.tools.types import ToolMetadata, ToolOutput

            from asap.integrations.llamaindex import LlamaIndexAsapTool

            llama_tool = LlamaIndexAsapTool(urn, client=client)
            assert isinstance(llama_tool.metadata, ToolMetadata)
            tool_output = asyncio.run(llama_tool.acall(input=expected))
            assert isinstance(tool_output, ToolOutput)
            output = tool_output.content

    resolve.assert_awaited_once_with(urn)
    agent.run.assert_awaited_once()
    assert json.loads(output) == expected
    assert not attempts, f"Network attempts: {attempts}"
    print(f"Checked {extra} with {framework} {version(framework)}.")


if __name__ == "__main__":
    main()
