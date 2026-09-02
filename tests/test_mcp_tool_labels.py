from types import SimpleNamespace

from bot import TelegramCodexBot, TurnSummary
from codex_client import ServerRequest


def test_mcp_label_identifies_save_draft_before_message_fallback() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot.thread_to_key = {}
    request = ServerRequest(
        id=1,
        method="mcpServer/elicitation/request",
        params={
            "_meta": {
                "tool_title": "Save Draft",
                "tool_params": {
                    "account": "main",
                    "chat_id": 1,
                    "message": "Draft text",
                },
            }
        },
    )

    assert bot._mcp_tool_label(request) == "telegram/save_draft"


def test_mcp_label_recognizes_old_save_draft_event_by_no_webpage() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot.thread_to_key = {}
    request = ServerRequest(
        id=2,
        method="mcpServer/elicitation/request",
        params={
            "_meta": {
                "tool_params": {
                    "account": "main",
                    "chat_id": "agent",
                    "message": "Draft text",
                    "no_webpage": True,
                }
            }
        },
    )

    assert bot._mcp_tool_label(request) == "telegram/save_draft"


def test_elicitation_correlates_to_in_progress_tool_without_item_id() -> None:
    bot = object.__new__(TelegramCodexBot)
    key = (1, "forum", 2)
    summary = TurnSummary(
        mcp_tools={"tool-item": "telegram/save_draft"},
        mcp_status={"tool-item": "inProgress"},
        mcp_arguments={
            "tool-item": {
                "account": "main",
                "chat_id": "agent",
                "message": "Draft text",
                "no_webpage": True,
            }
        },
    )
    bot.thread_to_key = {"thread": key}
    bot.sessions = {key: SimpleNamespace(turns={"turn": summary})}
    request = ServerRequest(
        id=3,
        method="mcpServer/elicitation/request",
        params={
            "threadId": "thread",
            "turnId": "turn",
            "serverName": "telegram",
            "mode": "form",
            "_meta": {
                "codex_approval_kind": "mcp_tool_call",
                "tool_params": {
                    "account": "main",
                    "chat_id": "agent",
                    "message": "Draft text",
                    "no_webpage": True,
                },
            },
        },
    )

    assert bot._pending_mcp_tool(request) == "telegram/save_draft"
    assert bot._mcp_tool_label(request) == "telegram/save_draft"
    assert bot._is_auto_approved_telegram_request(request)


def test_elicitation_uses_shared_tool_name_for_parallel_calls() -> None:
    bot = object.__new__(TelegramCodexBot)
    key = (1, "forum", 2)
    summary = TurnSummary(
        mcp_tools={
            "first": "telegram/download_media",
            "second": "telegram/download_media",
        },
        mcp_status={"first": "inProgress", "second": "inProgress"},
    )
    bot.thread_to_key = {"thread": key}
    bot.sessions = {key: SimpleNamespace(turns={"turn": summary})}
    request = ServerRequest(
        id=4,
        method="mcpServer/elicitation/request",
        params={
            "threadId": "thread",
            "turnId": "turn",
            "serverName": "telegram",
            "mode": "form",
            "_meta": {"codex_approval_kind": "mcp_tool_call"},
        },
    )

    assert bot._pending_mcp_tool(request) == "telegram/download_media"
    assert bot._mcp_tool_label(request) == "telegram/download_media"


def test_agent_telegram_approval_does_not_depend_on_item_correlation() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot.thread_to_key = {}
    request = ServerRequest(
        id=5,
        method="mcpServer/elicitation/request",
        params={
            "serverName": "telegram",
            "mode": "form",
            "_meta": {
                "codex_approval_kind": "mcp_tool_call",
                "tool_params": {"account": "agent"},
            },
        },
    )

    assert bot._is_auto_approved_telegram_request(request)
