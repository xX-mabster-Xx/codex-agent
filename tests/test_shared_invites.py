import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import CreateForumTopic

from bot import Session, TelegramCodexBot
from topic_sharing import ShareRegistry


OWNER = (101, "forum", 11)
GUEST = (202, "forum", 22)


def msg(user_id, topic_id=0, text="", *, shared=None, username=None):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, username=username, full_name=f"User {user_id}"),
        chat=SimpleNamespace(id=user_id, type="private"),
        message_thread_id=topic_id or None,
        direct_messages_topic=None,
        text=text,
        users_shared=shared,
        answer=AsyncMock(),
    )


def make_bot(tmp_path):
    bot = object.__new__(TelegramCodexBot)
    bot.config = SimpleNamespace(telegram_user_id=101)
    bot.shares = ShareRegistry(tmp_path / "shares.json", owner_id=101)
    bot.sessions = {OWNER: Session(OWNER, project_dir=tmp_path, topic_name="Shared work")}
    bot.bot_info = SimpleNamespace(username="test_bot")
    bot.bot = SimpleNamespace(
        create_forum_topic=AsyncMock(return_value=SimpleNamespace(
            message_thread_id=22, name="Shared work"
        )),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)),
    )
    bot._answer = AsyncMock()
    return bot


def test_link_join_creates_guest_topic(tmp_path):
    bot = make_bot(tmp_path)
    token = bot.shares.create_link(OWNER, now=time.time())
    guest = msg(202, text=f"/start {token}")

    asyncio.run(bot.on_start(guest))

    assert bot.shares.resolve(GUEST) == OWNER
    assert bot.shares.pending_for(202) == []
    assert bot.bot.create_forum_topic.await_args.kwargs["chat_id"] == 202
    assert bot.bot.send_message.await_count >= 1


def test_known_id_invite_notifies_now(tmp_path):
    bot = make_bot(tmp_path)
    bot.shares.record_user(202, "alex", "Alex", private_started=True)

    asyncio.run(bot.on_share(msg(101, 11, "/share @alex")))

    assert bot.shares.members(OWNER) == [(202, GUEST)]
    assert bot.bot.create_forum_topic.await_count == 1


def test_id_invite_activates_on_start(tmp_path):
    bot = make_bot(tmp_path)
    bot.bot.create_forum_topic.side_effect = TelegramForbiddenError(
        method=CreateForumTopic(chat_id=202, name="Shared work"),
        message="bot can't initiate conversation with a user",
    )
    selected = SimpleNamespace(request_id=42, users=[SimpleNamespace(
        user_id=202, username="alex", first_name="Alex", last_name=None,
    )])
    bot.shares.remember_selector(42, OWNER)

    asyncio.run(bot.on_users_shared(msg(101, 11, shared=selected)))
    assert bot.shares.pending_for(202) == [OWNER]
    assert bot.shares.members(OWNER) == []

    bot.bot.create_forum_topic.side_effect = None
    asyncio.run(bot.on_start(msg(202, text="/start", username="alex")))
    assert bot.shares.members(OWNER) == [(202, GUEST)]


def test_unknown_username_returns_link(tmp_path):
    bot = make_bot(tmp_path)

    asyncio.run(bot.on_share(msg(101, 11, "/share @never_seen")))

    assert bot.shares.members(OWNER) == []
    assert bot.bot.create_forum_topic.await_count == 0
    assert "https://t.me/test_bot?start=" in bot._answer.await_args.args[1]


def test_failed_topic_creation_keeps_id_pending(tmp_path):
    bot = make_bot(tmp_path)
    bot.shares.invite_user(OWNER, 202)
    bot.bot.create_forum_topic.side_effect = TelegramForbiddenError(
        method=CreateForumTopic(chat_id=202, name="Shared work"),
        message="bot can't initiate conversation with a user",
    )

    assert asyncio.run(bot._activate_pending(OWNER, 202)) is None
    assert bot.shares.pending_for(202) == [OWNER]
    assert bot.shares.members(OWNER) == []


def test_repeated_start_creates_one_topic(tmp_path):
    bot = make_bot(tmp_path)
    bot.shares.invite_user(OWNER, 202)

    asyncio.run(bot.on_start(msg(202, text="/start")))
    asyncio.run(bot.on_start(msg(202, text="/start")))

    assert bot.bot.create_forum_topic.await_count == 1
    assert bot.shares.members(OWNER) == [(202, GUEST)]


def test_selector_targets_original_topic(tmp_path):
    bot = make_bot(tmp_path)
    bot.sessions[101, "forum", 12] = Session((101, "forum", 12), project_dir=tmp_path)
    bot.shares.remember_selector(42, OWNER)
    selected = SimpleNamespace(request_id=42, users=[SimpleNamespace(
        user_id=202, username="alex", first_name="Alex", last_name=None,
    )])

    asyncio.run(bot.on_users_shared(msg(101, 12, shared=selected)))

    assert bot.shares.members(OWNER) == [(202, GUEST)]
    assert bot.shares.members((101, "forum", 12)) == []


def test_unshare_revokes_immediately(tmp_path):
    bot = make_bot(tmp_path)
    bot.shares.invite_user(OWNER, 202)
    bot.shares.attach(OWNER, 202, GUEST)

    asyncio.run(bot.on_unshare(msg(101, 11, "/unshare 202")))

    assert bot.shares.resolve(GUEST) is None
    assert bot._authorized_input(msg(202, 22, "hello")) is False
