from pathlib import Path

import pytest

from teleton_bridge.server import BridgeError, BridgeStore


@pytest.fixture
def store(tmp_path: Path) -> BridgeStore:
    return BridgeStore(tmp_path / "private" / "tasks.sqlite3")


def test_task_round_trip_between_agents(store: BridgeStore) -> None:
    task = store.create(
        actor="codex", target="teleton", kind="research", title="Inspect an SDK release",
        details="Return concise compatibility notes.", priority="high",
    )
    assert store.list_for_actor(actor="teleton", status="open", limit=20)[0]["id"] == task["id"]
    claimed = store.claim(actor="teleton")
    assert claimed is not None
    assert claimed["status"] == "claimed"
    complete = store.complete(actor="teleton", task_id=task["id"], result="Compatible with Node 26.")
    assert complete["status"] == "completed"
    assert complete["result"] == "Compatible with Node 26."


def test_actor_cannot_complete_another_agents_task(store: BridgeStore) -> None:
    task = store.create(
        actor="codex", target="teleton", kind="review", title="Review a patch", details="", priority="normal"
    )
    with pytest.raises(BridgeError, match="claimed"):
        store.complete(actor="codex", task_id=task["id"], result="not allowed")


def test_task_must_target_other_agent(store: BridgeStore) -> None:
    with pytest.raises(BridgeError, match="other agent"):
        store.create(
            actor="codex", target="codex", kind="note", title="invalid", details="", priority="low"
        )
