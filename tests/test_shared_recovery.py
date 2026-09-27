import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
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


def test_retry_after_is_bounded(tmp_path, monkeypatch):
    shares = registry(tmp_path)
    calls = []
    sleep = AsyncMock()
    monkeypatch.setattr("shared_delivery.asyncio.sleep", sleep)

    async def send_message(**kwargs):
        calls.append(kwargs)
        if kwargs["chat_id"] == 202:
            raise TelegramRetryAfter(method=SendMessage(chat_id=202, text="x"), message="retry", retry_after=3)
        return SimpleNamespace(message_id=len(calls))

    delivery = SharedDelivery(SimpleNamespace(send_message=send_message), shares)
    asyncio.run(delivery.broadcast_html(OWNER, "answer"))
    assert sum(c["chat_id"] == 202 for c in calls) == 2
    sleep.assert_awaited_once_with(3)


def test_long_retry_after_not_retried_prematurely(tmp_path):
    shares = registry(tmp_path)
    calls = []

    async def send_message(**kwargs):
        calls.append(kwargs)
        if kwargs["chat_id"] == 202:
            raise TelegramRetryAfter(method=SendMessage(chat_id=202, text="x"), message="retry", retry_after=60)
        return SimpleNamespace(message_id=len(calls))

    delivery = SharedDelivery(SimpleNamespace(send_message=send_message), shares)
    asyncio.run(delivery.broadcast_html(OWNER, "answer"))
    assert [c["chat_id"] for c in calls] == [101, 202]
    assert GUEST not in delivery.unavailable


def test_network_error_retries_once(tmp_path):
    shares = registry(tmp_path)
    attempts = 0

    async def send_message(**kwargs):
        nonlocal attempts
        if kwargs["chat_id"] == 202:
            attempts += 1
            if attempts == 1:
                raise TelegramNetworkError(method=SendMessage(chat_id=202, text="x"), message="temporary network")
        return SimpleNamespace(message_id=attempts + 1)

    delivery = SharedDelivery(SimpleNamespace(send_message=send_message), shares)
    asyncio.run(delivery.broadcast_html(OWNER, "answer"))
    assert attempts == 2


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


def test_consumed_link_survives_creation_failure_and_reload(tmp_path):
    shares = ShareRegistry(tmp_path / "shares.json", 101)
    token = shares.create_link(OWNER, now=time.time())
    assert shares.claim_link(token, 202, now=time.time()) == OWNER
    bot = object.__new__(TelegramCodexBot)
    bot.shares = shares
    bot.sessions = {OWNER: Session(OWNER, project_dir=tmp_path, topic_name="Shared")}
    bot.bot = SimpleNamespace(create_forum_topic=AsyncMock(side_effect=TelegramForbiddenError(
        method=SendMessage(chat_id=202, text="x"), message="bot was blocked by user",
    )))
    assert asyncio.run(bot._activate_pending(OWNER, 202)) is None
    bot.shares = ShareRegistry(tmp_path / "shares.json", 101)
    bot.bot.create_forum_topic.side_effect = None
    bot.bot.create_forum_topic.return_value = SimpleNamespace(message_thread_id=44)
    bot.bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
    assert asyncio.run(bot._activate_pending(OWNER, 202)) == (202, "forum", 44)


def test_wrong_topic_approval_does_not_consume_pending(tmp_path):
    bot = object.__new__(TelegramCodexBot)
    bot.config = SimpleNamespace(telegram_user_id=101)
    pending = SimpleNamespace(key=OWNER, message_id=9)
    bot.approvals = {"secret": pending}
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=101), data="approval:secret:yes",
        message=SimpleNamespace(
            chat=SimpleNamespace(id=101), message_thread_id=12,
            direct_messages_topic=None, message_id=9,
        ),
        answer=AsyncMock(),
    )
    asyncio.run(bot.on_approval(callback))
    assert bot.approvals["secret"] is pending
    callback.data = "approval_full:secret:15"
    asyncio.run(bot.on_approval_full_access(callback))
    assert bot.approvals["secret"] is pending


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


def test_concurrent_activation_creates_one_guest_topic(tmp_path):
    shares = ShareRegistry(tmp_path / "shares.json", 101)
    shares.invite_user(OWNER, 202)
    bot = object.__new__(TelegramCodexBot)
    bot.shares = shares
    bot.sessions = {OWNER: Session(OWNER, project_dir=tmp_path, topic_name="Shared")}
    created = 0

    async def create_forum_topic(**_kwargs):
        nonlocal created
        created += 1
        await asyncio.sleep(0)
        return SimpleNamespace(message_thread_id=20 + created)

    bot.bot = SimpleNamespace(
        create_forum_topic=create_forum_topic,
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
    )
    async def activate_twice():
        return await asyncio.gather(bot._activate_pending(OWNER, 202), bot._activate_pending(OWNER, 202))

    result = asyncio.run(activate_twice())
    assert result[0] == result[1]
    assert created == 1


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


def test_long_text_and_edit_are_split_with_attribution(tmp_path):
    shares = registry(tmp_path)
    sent = []

    async def send_message(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(message_id=len(sent))

    telegram = SimpleNamespace(send_message=send_message, edit_message_text=AsyncMock())
    delivery = SharedDelivery(telegram, shares)
    original = SimpleNamespace(chat=SimpleNamespace(id=202), message_id=7, text="🧪" * 3500)
    asyncio.run(delivery.mirror_human(original, GUEST, "Guest · 202"))
    assert len(sent) >= 2
    assert all(len(call["text"].encode("utf-16-le")) // 2 <= 4096 for call in sent)
    assert all("Guest · 202" in call["text"] for call in sent)
    original.text = "short edit"
    original.message_thread_id = 22
    asyncio.run(delivery.edit_human(original, "Guest · 202"))
    assert telegram.edit_message_text.await_count == len(sent)
    assert "short edit" in telegram.edit_message_text.await_args_list[0].args[0]


def test_unshared_owner_topic_unchanged(tmp_path):
    shares = registry(tmp_path)
    shares.revoke(OWNER, 202)
    telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    delivery = SharedDelivery(telegram, shares)
    asyncio.run(delivery.broadcast_html(OWNER, "owner only"))
    assert [c.kwargs["chat_id"] for c in telegram.send_message.await_args_list] == [101]


def test_revoke_stops_cached_agent_message_edits(tmp_path):
    shares = registry(tmp_path, two=True)
    serial = 0

    async def send_message(**kwargs):
        nonlocal serial
        serial += 1
        return SimpleNamespace(message_id=serial)

    telegram = SimpleNamespace(send_message=send_message, edit_message_text=AsyncMock())
    delivery = SharedDelivery(telegram, shares)
    owner = asyncio.run(delivery.broadcast_html(OWNER, "running"))
    shares.revoke(OWNER, 202)
    asyncio.run(delivery.broadcast_edit(OWNER, owner.message_id, "finished"))
    assert [c.kwargs["chat_id"] for c in telegram.edit_message_text.await_args_list] == [101, 303]
