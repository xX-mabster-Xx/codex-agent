import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot import Session, TelegramCodexBot
from topic_sharing import ShareRegistry


OWNER_KEY = (101, "forum", 11)
GUEST_KEY = (202, "forum", 22)


def message(user_id, chat_id, topic_id):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=chat_id, type="private"),
        message_thread_id=topic_id,
        direct_messages_topic=None,
    )


def make_bot(tmp_path):
    bot = object.__new__(TelegramCodexBot)
    bot.config = SimpleNamespace(telegram_user_id=101)
    bot.shares = ShareRegistry(tmp_path / "shares.json", owner_id=101)
    bot.sessions = {OWNER_KEY: Session(OWNER_KEY, project_dir=tmp_path)}
    return bot


def test_unknown_guest_creates_no_session(tmp_path):
    bot = make_bot(tmp_path)
    stranger = message(303, 303, 33)

    assert bot._authorized_input(stranger) is False
    assert bot._owner_only(stranger) is False
    assert len(bot.sessions) == 1


def test_linked_guest_uses_owner_session(tmp_path):
    bot = make_bot(tmp_path)
    bot.shares.invite_user(OWNER_KEY, 202)
    bot.shares.attach(OWNER_KEY, 202, GUEST_KEY)
    guest = message(202, 202, 22)

    assert bot._authorized_input(guest) is True
    assert bot._session(guest) is bot.sessions[OWNER_KEY]
    assert bot._session(message(101, 101, 11)) is bot.sessions[OWNER_KEY]


def test_guest_callback_cannot_reach_owner_handler(tmp_path):
    bot = make_bot(tmp_path)
    bot.shares.invite_user(OWNER_KEY, 202)
    bot.shares.attach(OWNER_KEY, 202, GUEST_KEY)
    callback = SimpleNamespace(
        from_user=SimpleNamespace(id=202),
        message=message(202, 202, 22),
        data="approval:token:yes",
    )

    assert bot._owner_only(callback) is False
    assert bot._owner_only(message(101, 101, 11)) is True


def test_guest_turn_downgrades_full_access(tmp_path):
    bot = make_bot(tmp_path)
    bot.full_access_until = 9_999_999_999.0
    bot.trusted_write_dirs = []
    session = bot.sessions[OWNER_KEY]
    session.thread_id = "thread-1"
    bot._ensure_thread = AsyncMock(return_value=False)
    bot._rpc = AsyncMock(return_value={"turn": {"id": "turn-1"}})
    bot._migration_input = lambda _session, items: items
    bot._record_context = lambda *_args: None
    bot._save_state = lambda: None

    asyncio.run(bot._start_user_turn(
        session, [{"type": "text", "text": "hello"}], guest_turn=True,
    ))

    params = bot._rpc.await_args.args[1]
    assert params["approvalPolicy"] == "on-request"
    assert params["sandboxPolicy"]["type"] == "workspaceWrite"
    assert params["sandboxPolicy"]["writableRoots"] == [str(Path(tmp_path))]
    assert bot._thread_access_params(session)["sandboxPolicy"]["type"] == "dangerFullAccess"
