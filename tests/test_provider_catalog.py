import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot import (
    MODEL_MENU_PAGE_SIZE,
    ModelMenuAction,
    ProviderDefinition,
    Session,
    TelegramCodexBot,
    discover_providers,
)


def test_discover_providers_reads_custom_codex_profile(tmp_path) -> None:
    (tmp_path / "example.config.toml").write_text(
        '''model_provider = "example"

[model_providers.example]
name = "Example API"
base_url = "https://api.example.test/v1/"
env_key = "EXAMPLE_API_KEY"
wire_api = "chat"

[model_providers.example.auth]
command = "/usr/local/bin/example-token"
args = ["--quiet"]
'''
    )

    providers = discover_providers(tmp_path)

    assert providers["example"] == ProviderDefinition(
        label="Example API",
        profile="example",
        base_url="https://api.example.test/v1",
        env_key="EXAMPLE_API_KEY",
        auth_command=("/usr/local/bin/example-token", "--quiet"),
    )


def test_large_catalog_is_grouped_and_paged() -> None:
    bot = object.__new__(TelegramCodexBot)
    key = (1, "forum", 2)
    bot.sessions = {key: Session(key=key, provider="openai")}
    bot.model_choices = {}
    bot.model_catalog_actions = {}
    sent = []

    async def send_html(*args):
        sent.append(args)

    bot._send_html = send_html
    models = [
        {"model": f"developer-{number}/model-{index}", "displayName": f"M {number}-{index}"}
        for number in range(11)
        for index in range(MODEL_MENU_PAGE_SIZE)
    ]

    asyncio.run(bot._render_model_menu(key, models))

    keyboard = sent[0][2]
    assert any("developer-0 · 12" in button.text for row in keyboard.inline_keyboard for button in row)
    assert any(button.text == "→" for row in keyboard.inline_keyboard for button in row)
    action = next(iter(bot.model_catalog_actions.values()))
    assert isinstance(action, ModelMenuAction)
    assert action.view == "models"


def test_catalog_page_replaces_its_existing_message() -> None:
    bot = object.__new__(TelegramCodexBot)
    key = (1, "forum", 2)
    bot.sessions = {key: Session(key=key, provider="openai")}
    bot.model_choices = {}
    bot.model_catalog_actions = {}
    bot._send_html = AsyncMock()
    message = SimpleNamespace(edit_text=AsyncMock())

    asyncio.run(bot._render_model_menu(
        key,
        [{"model": "developer/model", "displayName": "Model"}],
        replace_message=message,
    ))

    message.edit_text.assert_awaited_once()
    bot._send_html.assert_not_awaited()


def test_provider_catalog_accepts_codex_slug_shape() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot.config = SimpleNamespace(provider_secrets={}, proxy_url=None)
    bot._provider_catalog_request = AsyncMock(return_value={
        "models": [{"slug": "gonka-kimi", "display_name": "Gonka · Kimi"}],
    })

    models = asyncio.run(bot._provider_models(ProviderDefinition(
        label="Gonka", base_url="http://127.0.0.1:4011/v1",
    )))

    assert models == [{
        "slug": "gonka-kimi",
        "display_name": "Gonka · Kimi",
        "model": "gonka-kimi",
        "displayName": "Gonka · Kimi",
    }]


def test_provider_catalog_filters_explicitly_incompatible_capabilities() -> None:
    bot = object.__new__(TelegramCodexBot)
    bot.config = SimpleNamespace(provider_secrets={}, proxy_url=None)
    bot._provider_catalog_request = AsyncMock(return_value={
        "data": [
            {
                "id": "openai/gpt-5.6-luna",
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["tools", "temperature"],
            },
            {
                "id": "openai/gpt-5.6-luna:batch",
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["tools"],
            },
            {
                "id": "openai/gpt-audio",
                "architecture": {"input_modalities": ["audio", "text"], "output_modalities": ["text", "audio"]},
                "supported_parameters": ["tools"],
            },
            {
                "id": "plain-text-without-tools",
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "supported_parameters": ["temperature"],
            },
            # A legacy provider with no capability metadata remains visible.
            {"id": "unknown-capabilities"},
        ],
    })

    models = asyncio.run(bot._provider_models(ProviderDefinition(
        label="Example", base_url="http://127.0.0.1:4011/v1",
    )))

    assert [model["model"] for model in models] == [
        "openai/gpt-5.6-luna",
        "unknown-capabilities",
    ]


def test_price_label_uses_per_million_input_and_output_tokens() -> None:
    assert TelegramCodexBot._model_price_label({
        "pricing": {"prompt": "0.0000006400", "completion": "0.0000012800"},
    }) == "0.64/1.28 $/1M"


def test_button_label_removes_only_a_matching_leading_developer() -> None:
    assert TelegramCodexBot._model_button_label({
        "model": "openai/gpt-5.6",
        "displayName": "OpenAI: GPT-5.6",
    }) == "GPT-5.6"
    assert TelegramCodexBot._model_button_label({
        "model": "mistral/large",
        "displayName": "Mistral Large",
    }) == "Large"
    assert TelegramCodexBot._model_button_label({
        "model": "openai/gpt-5.6",
        "displayName": "GPT-5.6",
    }) == "GPT-5.6"
