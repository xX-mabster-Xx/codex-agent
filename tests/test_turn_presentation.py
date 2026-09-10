import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot import TelegramCodexBot, TurnSummary


def test_read_only_inspection_commands_are_quiet() -> None:
    quiet = (
        "sed -n '1,80p' bot.py",
        "rg -n reasoning bot.py",
        "find . -maxdepth 2 -type f",
        "git diff -- bot.py",
        "cat README.md",
        "/usr/bin/zsh -lc \"sed -n '1,80p' bot.py\"",
    )
    noisy = (
        "sed -i 's/old/new/' bot.py",
        "cat > output.txt",
        "pytest -q",
        "python -c 'Path(\"file\").write_text(\"x\")'",
    )

    assert all(TelegramCodexBot._is_low_signal_command(command) for command in quiet)
    assert not any(TelegramCodexBot._is_low_signal_command(command) for command in noisy)


def test_reasoning_is_appended_to_a_permanent_quote_message() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot._send_html = AsyncMock(return_value=SimpleNamespace(message_id=42))
    bot._edit_html = AsyncMock(return_value=True)
    summary = TurnSummary()
    key = (1, "forum", 2)

    asyncio.run(bot._append_reasoning(key, summary, "reasoning-1", "Сначала проверю файлы."))
    asyncio.run(bot._append_reasoning(key, summary, "reasoning-2", "Затем внесу исправление."))

    bot._send_html.assert_awaited_once()
    bot._edit_html.assert_awaited_once()
    edited_text = bot._edit_html.await_args.args[2]
    assert "Сначала проверю файлы." in edited_text
    assert "Затем внесу исправление." in edited_text
    assert edited_text.count("<blockquote") == 2


def test_activity_text_does_not_embed_transient_reasoning() -> None:
    summary = TurnSummary(reasoning_text="Старое размышление")

    text = TelegramCodexBot._activity_text(summary, None)

    assert "Старое размышление" not in text


def test_successful_quiet_command_has_no_command_message() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot._send_html = AsyncMock()
    bot._edit_html = AsyncMock(return_value=True)
    summary = TurnSummary(activity_message_id=100)
    key = (1, "forum", 2)
    item = {"type": "commandExecution", "id": "cmd-1", "command": "sed -n '1p' bot.py"}

    asyncio.run(bot._show_started_item(key, item, summary))
    asyncio.run(bot._show_completed_item(key, {
        **item,
        "status": "completed",
        "exitCode": 0,
    }, summary))

    bot._send_html.assert_not_awaited()


def test_failed_quiet_command_is_reported() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot._send_html = AsyncMock()
    bot._edit_html = AsyncMock(return_value=True)
    summary = TurnSummary(activity_message_id=100)
    key = (1, "forum", 2)
    item = {"type": "commandExecution", "id": "cmd-1", "command": "sed -n '1p' bot.py"}

    asyncio.run(bot._show_started_item(key, item, summary))
    asyncio.run(bot._show_completed_item(key, {
        **item,
        "status": "failed",
        "exitCode": 1,
        "aggregatedOutput": "file not found",
    }, summary))

    bot._send_html.assert_awaited_once()
    assert "file not found" in bot._send_html.await_args.args[1]
