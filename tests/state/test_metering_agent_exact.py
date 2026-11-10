"""State metering selects an agent id with equality, not containment.

In-memory ``query`` and ``aggregate`` and the SQLite ``agent_id = ?``
statements are separate. A neighbor such as ``agent_010`` must not add
tokens or rows to ``agent_01``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from asap.state.metering import InMemoryMeteringStore, UsageEvent, UsageMetrics
from asap.state.stores.sqlite import SQLiteMeteringStore

_STAMP = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
_START = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
_END = datetime(2026, 6, 15, 23, 0, tzinfo=timezone.utc)
_EXACT = "agent_01"
_EXACT_TOKENS = 10
# token count is unique so a widened aggregate cannot still total 10.
_NEIGHBORS: tuple[tuple[str, int], ...] = (
    ("agent_010", 100),
    ("agent_0", 1),
    ("Agent_01", 7),
    ("agent_01 ", 3),
    (" agent_01", 4),
)


def _event(agent_id: str, tokens_in: int) -> UsageEvent:
    """One metered event for ``agent_id``."""
    return UsageEvent(
        task_id="task_01",
        agent_id=agent_id,
        consumer_id="consumer_01",
        metrics=UsageMetrics(tokens_in=tokens_in),
        timestamp=_STAMP,
    )


async def _record(store: InMemoryMeteringStore | SQLiteMeteringStore) -> None:
    """Store the exact agent and every neighbor."""
    await store.record(_event(_EXACT, _EXACT_TOKENS))
    for agent_id, tokens_in in _NEIGHBORS:
        await store.record(_event(agent_id, tokens_in))


@pytest.mark.asyncio
async def test_state_metering_agent_id_match_is_exact(tmp_path: Path) -> None:
    """Query and aggregate on both state stores keep ``agent_01`` alone."""
    memory = InMemoryMeteringStore()
    sqlite = SQLiteMeteringStore(db_path=tmp_path / "state-agent-exact.db")
    await _record(memory)
    await _record(sqlite)

    for store in (memory, sqlite):
        found = await store.query(_EXACT, _START, _END)
        assert [event.agent_id for event in found] == [_EXACT]
        aggregate = await store.aggregate(_EXACT, "day")
        assert aggregate.agent_id == _EXACT
        assert aggregate.total_tasks == 1
        assert aggregate.total_tokens == _EXACT_TOKENS
