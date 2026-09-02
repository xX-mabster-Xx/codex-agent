import asyncio
from unittest.mock import AsyncMock

from bot import QueuedInput, Session, TelegramCodexBot


def test_steer_active_turn_appends_context_to_current_turn() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot._rpc = AsyncMock(return_value={"turnId": "turn-1"})
    session = Session(key=(1, "forum", 2), thread_id="thread-1", active_turn_id="turn-1")
    input_items = [{"type": "text", "text": "Use this additional context."}]

    assert asyncio.run(bot._steer_active_turn(session, input_items))
    bot._rpc.assert_awaited_once_with(
        "turn/steer",
        {
            "threadId": "thread-1",
            "input": input_items,
            "expectedTurnId": "turn-1",
        },
        timeout=10,
    )


def test_busy_input_offers_steering_only_for_active_turn() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot.busy_inputs = {}
    bot._send_html = AsyncMock()
    session = Session(key=(1, "forum", 2), thread_id="thread-1", active_turn_id="turn-1")

    asyncio.run(bot._offer_busy_input(session, QueuedInput([{"type": "text", "text": "Context"}], 7)))

    keyboard = bot._send_html.await_args.args[2]
    assert keyboard.inline_keyboard[0][0].callback_data.endswith(":steer")
    assert len(keyboard.inline_keyboard) == 2
