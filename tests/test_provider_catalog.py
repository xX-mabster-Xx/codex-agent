import asyncio

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
