from bot import TelegramCodexBot


def test_formats_codex_subscription_limits_from_rpc_response() -> None:
    text = TelegramCodexBot._format_codex_limits(
        {
            "planType": "plus",
            "primary": {
                "usedPercent": 32,
                "windowDurationMins": 300,
                "resetsAt": 1_790_000_000,
            },
            "secondary": {
                "usedPercent": 5,
                "windowDurationMins": 10_080,
                "resetsAt": 1_790_100_000,
            },
        },
        stale=False,
    )

    assert "Лимиты подписки Codex" in text
    assert "plus" in text
    assert "68%" in text
    assert "95%" in text
    assert "5 часов" in text
    assert "7 дней" in text


def test_formats_snake_case_rate_limit_event() -> None:
    text = TelegramCodexBot._format_codex_limits(
        {
            "plan_type": "plus",
            "primary": {
                "used_percent": 100,
                "window_minutes": 300,
                "resets_at": 1_790_000_000,
            },
        },
        stale=True,
    )

    assert "0%" in text
    assert "последние полученные" in text
