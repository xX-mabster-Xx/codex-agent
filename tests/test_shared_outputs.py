import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import Session, TelegramCodexBot, TurnSummary
from codex_client import ServerRequest
from shared_delivery import SharedDelivery
from topic_sharing import ShareRegistry


OWNER = (101, "forum", 11)
GUEST = (202, "forum", 22)


def delivery(tmp_path):
    shares = ShareRegistry(tmp_path / "shares.json", owner_id=101)
    shares.invite_user(OWNER, 202)
    shares.attach(OWNER, 202, GUEST)
    serial = {101: 10, 202: 20}

    async def send_message(*_args, **kwargs):
        chat_id = kwargs.get("chat_id", _args[0] if _args else None)
        if _args:
            kwargs = {"chat_id": chat_id, "text": _args[1], **kwargs}
        serial[chat_id] += 1
        calls.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=serial[chat_id])

    async def send_rich_message(*_args, **kwargs):
        return await send_message(**kwargs)

    calls = []
    telegram = SimpleNamespace(
        send_message=send_message,
        send_rich_message=send_rich_message,
        edit_message_text=AsyncMock(),
    )
    return SharedDelivery(telegram, shares), telegram, calls


def test_agent_final_reaches_all_members(tmp_path):
    shared, _, calls = delivery(tmp_path)

    owner_messages = asyncio.run(shared.broadcast_markdown(OWNER, "**Finished**", silent=True))

    assert len(owner_messages) == 1
    assert {call["chat_id"] for call in calls} == {101, 202}
    assert all(call["message_thread_id"] in {11, 22} for call in calls)


def test_status_edit_uses_each_chat_message_id(tmp_path):
    shared, telegram, _ = delivery(tmp_path)
    owner_message = asyncio.run(shared.broadcast_html(OWNER, "Working", silent=True))

    asyncio.run(shared.broadcast_edit(OWNER, owner_message.message_id, "Finished"))

    assert {(call.kwargs["chat_id"], call.kwargs["message_id"])
            for call in telegram.edit_message_text.await_args_list} == {(101, 11), (202, 21)}


def test_approval_buttons_owner_only(tmp_path):
    shared, telegram, calls = delivery(tmp_path)
    bot = object.__new__(TelegramCodexBot)
    bot.bot = telegram
    bot.shares = shared.shares
    bot.agent_message_topics = {}
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Approve", callback_data="approval:secret:yes")
    ]])

    asyncio.run(bot._send_html(OWNER, "Approval required", keyboard))

    assert [call["chat_id"] for call in calls] == [101]
    assert calls[0]["reply_markup"] is keyboard


def test_guest_sees_read_only_approval_status(tmp_path):
    shared, _, calls = delivery(tmp_path)

    asyncio.run(shared.notify_approval_waiting(OWNER))

    assert [call["chat_id"] for call in calls] == [202]
    assert calls[0].get("reply_markup") is None
    assert "владельц" in calls[0]["text"].lower()


def test_control_menus_stay_owner_only(tmp_path):
    shared, telegram, calls = delivery(tmp_path)
    bot = object.__new__(TelegramCodexBot)
    bot.bot = telegram
    bot.shares = shared.shares
    bot.agent_message_topics = {}

    asyncio.run(bot._send_html(OWNER, "Model selection"))

    assert [call["chat_id"] for call in calls] == [101]


def test_completed_agent_message_uses_shared_delivery(tmp_path):
    shared, telegram, calls = delivery(tmp_path)
    bot = object.__new__(TelegramCodexBot)
    bot.bot = telegram
    bot.shares = shared.shares
    bot.shared_delivery = shared
    bot.agent_message_topics = {}
    bot._update_activity = AsyncMock()

    asyncio.run(bot._show_completed_item(
        OWNER, {"type": "agentMessage", "id": "a", "text": "Shared answer"},
        TurnSummary(),
    ))

    assert {call["chat_id"] for call in calls} == {101, 202}


def test_agent_activity_uses_shared_delivery(tmp_path):
    shared, telegram, calls = delivery(tmp_path)
    bot = object.__new__(TelegramCodexBot)
    bot.bot = telegram
    bot.shares = shared.shares
    bot.shared_delivery = shared
    bot.agent_message_topics = {}

    asyncio.run(bot._update_activity(OWNER, TurnSummary(activity_title="Working"), force=True))

    assert {call["chat_id"] for call in calls} == {101, 202}


def test_guest_request_never_uses_owner_full_access(tmp_path):
    shared, telegram, calls = delivery(tmp_path)
    bot = object.__new__(TelegramCodexBot)
    bot.bot = telegram
    bot.shares = shared.shares
    bot.shared_delivery = shared
    bot.sessions = {OWNER: Session(OWNER, active_guest_turn=True)}
    bot.thread_to_key = {"thread-1": OWNER}
    bot.approvals = {}
    client = SimpleNamespace(respond=AsyncMock())
    bot._client = lambda _provider: client
    bot._send_html = AsyncMock(return_value=SimpleNamespace(
        message_id=1, edit_reply_markup=AsyncMock(),
    ))
    bot._approval_text = lambda _provider, _request: "Approve command"
    for name in (
        "_is_google_calendar_mcp_request", "_is_memory_mcp_request",
        "_is_auto_approved_telegram_request", "_is_agent_scheduler_follow_up_request",
        "_is_native_bot_delivery_request", "_is_safe_file_change", "_is_mcp_tool_approval",
    ):
        setattr(bot, name, lambda *_args: False)
    bot._should_auto_approve_with_full_access = lambda _request: True
    request = ServerRequest(
        id="approval-1", method="item/commandExecution/requestApproval",
        params={"threadId": "thread-1", "command": "echo hello"},
    )

    asyncio.run(bot._handle_request("openai", request))

    client.respond.assert_not_awaited()
    assert len(bot.approvals) == 1
    assert bot._send_html.await_args.kwargs["silent"] is False
    assert bot._send_html.await_args.args[0] == OWNER
    assert [call["chat_id"] for call in calls] == [202]
    assert calls[0].get("reply_markup") is None


def test_guest_cannot_call_approval_handler_directly(tmp_path):
    shared, _, _ = delivery(tmp_path)
    bot = object.__new__(TelegramCodexBot)
    bot.config = SimpleNamespace(telegram_user_id=101)
    bot.shares = shared.shares
    bot.approvals = {"token": object()}
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=202), data="approval:token:yes",
        answer=AsyncMock(), message=SimpleNamespace(chat=SimpleNamespace(id=202)),
    )

    asyncio.run(bot.on_approval(callback))

    assert "token" in bot.approvals
    callback.answer.assert_awaited_once()
