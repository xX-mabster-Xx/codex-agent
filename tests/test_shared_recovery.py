import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage

from bot import Session, TelegramCodexBot
from shared_delivery import SharedDelivery
from topic_sharing import ShareRegistry


OWNER = (101, "forum", 11)
GUEST = (202, "forum", 22)
OTHER = (303, "forum", 33)


def registry(tmp_path, two=False):
    shares = ShareRegistry(tmp_path / "shares.json", 101)
    for uid, key in [(202, GUEST), (303, OTHER)][:2 if two else 1]:
        shares.invite_user(OWNER, uid)
        shares.attach(OWNER, uid, key)
    return shares


def test_blocked_guest_isolated_and_owner_notified_once(tmp_path):
    shares = registry(tmp_path, two=True)
    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)
        if kwargs["chat_id"] == 202:
            raise TelegramForbiddenError(method=SendMessage(chat_id=202, text="x"), message="bot was blocked by user")
        return SimpleNamespace(message_id=len(calls))

    delivery = SharedDelivery(SimpleNamespace(send_message=send_message), shares)
    asyncio.run(delivery.broadcast_html(OWNER, "first"))
    asyncio.run(delivery.broadcast_html(OWNER, "second"))
    assert [c["text"] for c in calls if c["chat_id"] == 303] == ["first", "second"]
    assert sum("недоступ" in c["text"].lower() for c in calls if c["chat_id"] == 101) == 1


def test_retry_after_is_bounded(tmp_path):
    shares = registry(tmp_path)
    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)
        if kwargs["chat_id"] == 202:
            raise TelegramRetryAfter(method=SendMessage(chat_id=202, text="x"), message="retry", retry_after=0)
        return SimpleNamespace(message_id=len(calls))

    delivery = SharedDelivery(SimpleNamespace(send_message=send_message), shares)
    asyncio.run(delivery.broadcast_html(OWNER, "answer"))
    assert sum(c["chat_id"] == 202 for c in calls) == 2


def test_formatting_error_does_not_mark_guest_unavailable(tmp_path):
    shares = registry(tmp_path)
    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)
        if kwargs["chat_id"] == 202:
            raise TelegramBadRequest(method=SendMessage(chat_id=202, text="x"), message="can't parse entities")
        return SimpleNamespace(message_id=len(calls))

    delivery = SharedDelivery(SimpleNamespace(send_message=send_message), shares)
    asyncio.run(delivery.broadcast_html(OWNER, "broken markup"))
    assert GUEST not in delivery.unavailable
    assert [c["chat_id"] for c in calls] == [101, 202]


def test_reload_restores_pending_and_members(tmp_path):
    shares = registry(tmp_path)
    shares.invite_user(OWNER, 303)
    loaded = ShareRegistry(tmp_path / "shares.json", 101)
    assert loaded.members(OWNER) == [(202, GUEST)]
    assert loaded.pending_for(303) == [OWNER]
    assert loaded.linked_for(202) == [(OWNER, GUEST)]


def test_link_for_existing_member_does_not_leave_pending(tmp_path):
    shares = registry(tmp_path)
    token = shares.create_link(OWNER, now=time.time())
    assert shares.claim_link(token, 202, now=time.time()) == OWNER
    assert shares.pending_for(202) == []


def test_deleted_guest_topic_recreated_on_start(tmp_path):
    shares = registry(tmp_path)
    bot = object.__new__(TelegramCodexBot)
    bot.config = SimpleNamespace(telegram_user_id=101)
    bot.shares = shares
    bot.sessions = {OWNER: Session(OWNER, project_dir=tmp_path, topic_name="Shared")}
    async def send_message(**kwargs):
        if kwargs.get("message_thread_id") == 22:
            raise TelegramBadRequest(method=SendMessage(chat_id=202, text="x"), message="message thread not found")
        return SimpleNamespace(message_id=1)
    bot.bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=send_message),
        create_forum_topic=AsyncMock(return_value=SimpleNamespace(message_thread_id=44)),
    )
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=202, username="guest", full_name="Guest"),
        chat=SimpleNamespace(id=202, type="private"), message_thread_id=None,
        text="/start", answer=AsyncMock(),
    )
    asyncio.run(bot.on_start(message))
    assert shares.resolve((202, "forum", 44)) == OWNER
    assert shares.resolve(GUEST) is None
    bot.bot.create_forum_topic.assert_awaited_once()


def test_edited_text_updates_copy(tmp_path):
    shares = registry(tmp_path)
    telegram = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
        edit_message_text=AsyncMock(),
    )
    delivery = SharedDelivery(telegram, shares)
    original = SimpleNamespace(chat=SimpleNamespace(id=202), message_id=7, text="old")
    asyncio.run(delivery.mirror_human(original, GUEST, "Guest · 202"))
    bot = object.__new__(TelegramCodexBot)
    bot.config = SimpleNamespace(telegram_user_id=101)
    bot.shares = shares
    bot.shared_delivery = delivery
    edited = SimpleNamespace(
        chat=SimpleNamespace(id=202, type="private"), from_user=SimpleNamespace(id=202, full_name="Guest"),
        message_thread_id=22, direct_messages_topic=None, message_id=7, text="new",
    )
    asyncio.run(bot.on_edited_message(edited))
    assert telegram.edit_message_text.await_args.kwargs["chat_id"] == 101
    assert "new" in telegram.edit_message_text.await_args.args[0]


def test_unshared_owner_topic_unchanged(tmp_path):
    shares = registry(tmp_path)
    shares.revoke(OWNER, 202)
    telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    delivery = SharedDelivery(telegram, shares)
    asyncio.run(delivery.broadcast_html(OWNER, "owner only"))
    assert [c.kwargs["chat_id"] for c in telegram.send_message.await_args_list] == [101]
