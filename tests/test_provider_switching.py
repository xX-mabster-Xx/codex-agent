import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from bot import PROVIDERS, Session, TelegramCodexBot


def test_provider_switch_restores_each_native_thread() -> None:
    bot = object.__new__(TelegramCodexBot)
    session = Session(
        key=(1, "forum", 2),
        thread_id="openai-thread",
        model="gpt-5.6-luna",
        reasoning_effort="high",
        attached=True,
    )

    bot._activate_provider(session, "gonka")
    assert session.provider == "gonka"
    assert session.thread_id is None
    assert session.model is None
    assert session.provider_states["openai"].thread_id == "openai-thread"

    session.thread_id = "gonka-thread"
    session.model = "gonka-deepseek"
    session.reasoning_effort = "medium"
    session.attached = True
    bot._activate_provider(session, "openai")

    assert session.thread_id == "openai-thread"
    assert session.model == "gpt-5.6-luna"
    assert session.reasoning_effort == "high"
    assert session.attached is True


def test_first_provider_turn_gets_topic_continuity_transcript() -> None:
    bot = object.__new__(TelegramCodexBot)
    session = Session(key=(1, "forum", 2), provider="gonka")
    session.context_log = [
        ("user", "Please keep the API backwards compatible."),
        ("assistant", "I will preserve the public API."),
    ]
    session.pending_context_providers.add("gonka")

    items = bot._migration_input(session, [{"type": "text", "text": "Implement it."}])

    assert len(items) == 2
    assert "Please keep the API backwards compatible." in items[0]["text"]
    assert "I will preserve the public API." in items[0]["text"]
    assert items[1]["text"] == "Implement it."


def test_custom_provider_picker_uses_its_catalog() -> None:
    bot = object.__new__(TelegramCodexBot)
    session = Session(key=(1, "forum", 2), provider="gonka")
    bot._provider_models = AsyncMock(return_value=[
        {"model": "gonka/example", "displayName": "Example"},
    ])

    models = asyncio.run(bot._models_for_provider(session))

    assert [model["model"] for model in models] == ["gonka/example"]


def test_thread_access_uses_current_app_server_sandbox_enum() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot.full_access_until = 0.0
    bot.trusted_write_dirs = [Path("/tmp/shared")]
    session = Session(
        key=(1, "forum", 2),
        project_dir=Path("/tmp/project"),
    )

    assert bot._thread_access_params(session) == {
        "approvalPolicy": "on-request",
        "sandboxPolicy": {
            "type": "workspaceWrite",
            "writableRoots": ["/tmp/project", "/tmp/shared"],
        },
    }


def test_full_access_uses_current_app_server_sandbox_enum() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot.full_access_until = 9_999_999_999.0
    bot.trusted_write_dirs = []
    session = Session(key=(1, "forum", 2), project_dir=Path("/tmp/project"))

    assert bot._thread_access_params(session) == {
        "approvalPolicy": "never",
        "sandboxPolicy": {"type": "dangerFullAccess"},
    }


def test_model_picker_includes_per_topic_provider_buttons() -> None:
    bot = object.__new__(TelegramCodexBot)
    key = (1, "forum", 2)
    session = Session(key=key, provider="gonka")
    bot.sessions = {key: session}
    bot.model_choices = {}
    bot.model_catalog_actions = {}
    bot._provider_models = AsyncMock(return_value=[
        {"model": "gonka/example", "displayName": "Example"},
    ])
    bot._send_html = AsyncMock()

    asyncio.run(bot._show_model_menu(key))

    _, _, keyboard = bot._send_html.await_args.args
    provider_buttons = [
        button
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data and button.callback_data.startswith("provider:set:")
    ]
    assert [button.callback_data for button in provider_buttons] == [
        f"provider:set:{name}" for name in PROVIDERS
    ]
    assert provider_buttons[1].text.startswith("✓ ")
