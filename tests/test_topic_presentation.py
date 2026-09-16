from types import SimpleNamespace

from bot import DEFAULT_NEW_TOPIC_MODEL, Session, TelegramCodexBot


def test_topic_presentation_keeps_only_three_meaningful_words() -> None:
    assert TelegramCodexBot._topic_presentation("Разработка Telegram MCP") == (
        "Разработка Telegram MCP",
        "💻",
    )


def test_topic_presentation_selects_finance_icon() -> None:
    assert TelegramCodexBot._topic_presentation("TON финансы и трейдинг") == (
        "TON Финансы Трейдинг",
        "💰",
    )


def test_topic_presentation_uses_fallback_for_generated_name() -> None:
    assert TelegramCodexBot._topic_presentation("Codex 2026-08-26") == (
        "Новая задача",
        "💡",
    )


def test_new_ordinary_topic_uses_luna_without_model_picker() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot._is_agent_session = lambda session: False
    bot._save_state = lambda: None
    session = Session(key=(1, "forum", 2))

    assert bot._require_model_selection(session) is False
    assert session.model == DEFAULT_NEW_TOPIC_MODEL
    assert not session.awaiting_model_selection


def test_split_topic_session_reuses_explicit_project_dir(tmp_path) -> None:
    bot = object.__new__(TelegramCodexBot)
    bot.sessions = {}
    bot.config = SimpleNamespace(
        agent_topic_id=None,
        agent_topic_name="",
        projects_root=tmp_path / "projects",
        project_dir=tmp_path / "default",
    )
    bot._save_state = lambda: None
    shared = tmp_path / "shared"

    session = bot._topic_session(1, 22, "Split", project_dir=shared)

    assert session.project_dir == shared
    assert shared.is_dir()
