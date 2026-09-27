import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot import Session, TelegramCodexBot
from shared_delivery import SharedDelivery
from topic_sharing import ShareRegistry


OWNER = (101, "forum", 11)
GUEST_A = (202, "forum", 22)
GUEST_B = (303, "forum", 33)


def make_registry(tmp_path, *, two_guests=False):
    registry = ShareRegistry(tmp_path / "shares.json", owner_id=101)
    for user_id, key in [(202, GUEST_A), (303, GUEST_B)][:2 if two_guests else 1]:
        registry.invite_user(OWNER, user_id)
        registry.attach(OWNER, user_id, key)
    return registry


def message(user_id, topic_id, *, text=None, content_type="text"):
    return SimpleNamespace(
        chat=SimpleNamespace(id=user_id, type="private"),
        from_user=SimpleNamespace(id=user_id, full_name=f"User {user_id}"),
        message_thread_id=topic_id,
        direct_messages_topic=None,
        message_id=55,
        text=text,
        content_type=content_type,
        answer=AsyncMock(),
    )


def test_text_mirrors_both_directions(tmp_path):
    registry = make_registry(tmp_path)
    telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)))
    delivery = SharedDelivery(telegram, registry)

    asyncio.run(delivery.mirror_human(message(101, 11, text="hello"), OWNER, "Owner · 101"))
    assert telegram.send_message.await_args.kwargs["chat_id"] == 202
    assert telegram.send_message.await_args.kwargs["message_thread_id"] == 22
    assert "Owner · 101" in telegram.send_message.await_args.kwargs["text"]
    assert "hello" in telegram.send_message.await_args.kwargs["text"]

    asyncio.run(delivery.mirror_human(message(202, 22, text="reply"), GUEST_A, "Alex · 202"))
    assert telegram.send_message.await_args.kwargs["chat_id"] == 101
    assert "Alex · 202" in telegram.send_message.await_args.kwargs["text"]


def test_two_guests_receive_other_guest_message(tmp_path):
    registry = make_registry(tmp_path, two_guests=True)
    telegram = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=99)))
    delivery = SharedDelivery(telegram, registry)

    asyncio.run(delivery.mirror_human(message(202, 22, text="update"), GUEST_A, "Alex · 202"))

    assert {call.kwargs["chat_id"] for call in telegram.send_message.await_args_list} == {101, 303}
    assert all(call.kwargs["message_thread_id"] in {11, 33} for call in telegram.send_message.await_args_list)


def test_media_copy_targets_correct_thread(tmp_path):
    registry = make_registry(tmp_path)
    telegram = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=98)),
        copy_message=AsyncMock(return_value=SimpleNamespace(message_id=99)),
    )
    delivery = SharedDelivery(telegram, registry)

    asyncio.run(delivery.mirror_human(message(202, 22, content_type="document"), GUEST_A, "Alex · 202"))

    assert telegram.copy_message.await_args.kwargs == {
        "chat_id": 101,
        "message_thread_id": 11,
        "from_chat_id": 202,
        "message_id": 55,
        "disable_notification": True,
    }
    assert telegram.send_message.await_args.kwargs["chat_id"] == 101


def make_bot(tmp_path):
    bot = object.__new__(TelegramCodexBot)
    bot.config = SimpleNamespace(telegram_user_id=101)
    bot.shares = make_registry(tmp_path)
    bot.sessions = {OWNER: Session(OWNER, project_dir=tmp_path)}
    bot._start_queued_input = AsyncMock(return_value=True)
    return bot


def test_author_id_reaches_codex_input(tmp_path):
    bot = make_bot(tmp_path)
    guest = message(202, 22, text="fix it")

    asyncio.run(bot._submit_input(guest, [{"type": "text", "text": "fix it"}], input_chars=6))

    queued = bot._start_queued_input.await_args.args[1]
    assert queued.origin_user_id == 202
    assert "Telegram-ID: 202" in queued.input_items[0]["text"]
    assert queued.input_items[1]["text"] == "fix it"


def test_queued_guest_turn_retains_origin_and_policy(tmp_path):
    bot = make_bot(tmp_path)
    bot.sessions[OWNER].active_turn_id = "busy"
    bot._offer_busy_input = AsyncMock()

    asyncio.run(bot._submit_input(
        message(202, 22, text="next"), [{"type": "text", "text": "next"}], input_chars=4,
    ))

    queued = bot._offer_busy_input.await_args.args[1]
    assert queued.origin_user_id == 202
    assert queued.guest_turn is True
    assert "Telegram-ID: 202" in queued.input_items[0]["text"]


def test_unsupported_media_does_not_start_turn(tmp_path):
    bot = make_bot(tmp_path)
    bot._submit_input = AsyncMock()
    sticker = message(202, 22, content_type="sticker")

    asyncio.run(bot.on_unsupported_media(sticker))

    sticker.answer.assert_awaited_once()
    bot._submit_input.assert_not_awaited()
