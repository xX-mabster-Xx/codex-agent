import asyncio

from bot import Session, TelegramCodexBot


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


def test_gonka_picker_has_only_gonka_models() -> None:
    bot = object.__new__(TelegramCodexBot)
    session = Session(key=(1, "forum", 2), provider="gonka")

    models = asyncio.run(bot._models_for_provider(session))

    assert [model["model"] for model in models] == [
        "gonka-minimax",
        "gonka-deepseek",
        "gonka-kimi",
    ]
