import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from bot import Session, SubagentState, TelegramCodexBot
from codex_client import CodexClient


def _bot_with_root_thread() -> tuple[TelegramCodexBot, tuple[int, str, int], Session]:
    bot = object.__new__(TelegramCodexBot)
    key = (1, "forum", 2)
    session = Session(
        key=key,
        provider="openai",
        thread_id="root-thread",
        active_turn_id="root-turn",
    )
    bot.sessions = {key: session}
    bot.thread_to_key = {"root-thread": key}
    bot.thread_provider = {"root-thread": "openai"}
    bot.subagents = {}
    bot.subagent_status_messages = {}
    bot.subagent_status_updated_at = {}
    bot.subagent_stop_actions = {}
    return bot, key, session


def test_unknown_child_event_is_bound_to_its_root_thread() -> None:
    bot, key, session = _bot_with_root_thread()
    bot._rpc = AsyncMock(return_value={
        "data": [{"id": "child-thread", "name": "Test explorer"}],
    })
    bot._update_subagent_status = AsyncMock()
    bot._finish_turn = AsyncMock()

    asyncio.run(bot._handle_event("openai", "turn/started", {
        "threadId": "child-thread",
        "turn": {"id": "child-turn"},
    }))

    child = bot.subagents["child-thread"]
    assert child.key == key
    assert child.root_thread_id == "root-thread"
    assert child.active_turn_id == "child-turn"
    assert bot.thread_to_key["child-thread"] == key
    assert session.active_turn_id == "root-turn"

    asyncio.run(bot._handle_event("openai", "turn/completed", {
        "threadId": "child-thread",
        "turn": {"id": "child-turn", "status": "completed"},
    }))

    assert child.status == "completed"
    assert session.active_turn_id == "root-turn"
    bot._finish_turn.assert_not_awaited()


def test_subagent_approval_is_marked_for_the_parent_topic() -> None:
    bot, key, _ = _bot_with_root_thread()
    bot.subagents["child-thread"] = SubagentState(
        thread_id="child-thread",
        key=key,
        provider="openai",
        root_thread_id="root-thread",
        label="Security review",
    )

    from codex_client import ServerRequest

    text = bot._approval_text("openai", ServerRequest(
        id=1,
        method="item/commandExecution/requestApproval",
        params={"threadId": "child-thread", "command": "pytest -q"},
    ))

    assert "Subagent:</b> Security review" in text


def test_permission_approval_grants_only_the_requested_subset() -> None:
    from codex_client import ServerRequest

    request = ServerRequest(
        id=1,
        method="item/permissions/requestApproval",
        params={"permissions": [{"kind": "network", "host": "example.com"}]},
    )

    assert TelegramCodexBot._approval_response(request, True) == {
        "permissions": [{"kind": "network", "host": "example.com"}],
        "scope": "turn",
    }
    assert TelegramCodexBot._approval_response(request, False) == {
        "permissions": [],
        "scope": "turn",
    }


def test_stop_subagents_interrupts_children_not_the_root() -> None:
    bot, key, _ = _bot_with_root_thread()
    bot.subagents["child-thread"] = SubagentState(
        thread_id="child-thread",
        key=key,
        provider="openai",
        root_thread_id="root-thread",
        label="Tests",
        active_turn_id="child-turn",
    )
    bot._cancel_approvals = AsyncMock()
    bot._update_subagent_status = AsyncMock()
    bot._rpc = AsyncMock(return_value={})

    stopped = asyncio.run(bot._stop_subagents(key, "openai", "root-thread"))

    assert stopped == 1
    bot._rpc.assert_awaited_once_with(
        "turn/interrupt",
        {"threadId": "child-thread", "turnId": "child-turn"},
        provider="openai",
        timeout=8,
    )


def test_runtime_subagent_overrides_follow_profile_overrides() -> None:
    client = CodexClient(
        project_dir=Path("/tmp"),
        profile=None,
        runtime_config_overrides=[
            "agents.enabled=true",
            "agents.max_concurrent_threads_per_session=3",
        ],
    )

    assert client.config_overrides == [
        "agents.enabled=true",
        "agents.max_concurrent_threads_per_session=3",
    ]
