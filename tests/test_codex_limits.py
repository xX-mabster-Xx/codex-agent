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


def test_five_hour_rollover_is_not_a_full_reset() -> None:
    previous = TelegramCodexBot._codex_limits_snapshot(
        {
            "primary": {"usedPercent": 80, "resetsAt": 1_800_001_000},
            "secondary": {"usedPercent": 40, "resetsAt": 1_800_100_000},
        }
    )
    current = TelegramCodexBot._codex_limits_snapshot(
        {
            "primary": {"usedPercent": 0, "resetsAt": 1_800_019_000},
            "secondary": {"usedPercent": 40, "resetsAt": 1_800_100_000},
        }
    )

    assert not TelegramCodexBot._is_unscheduled_full_limits_reset(
        previous, current, now=1_800_019_000
    )


def test_early_full_reset_is_detected() -> None:
    previous = TelegramCodexBot._codex_limits_snapshot(
        {
            "primary": {"usedPercent": 80, "resetsAt": 1_800_001_000},
            "secondary": {"usedPercent": 40, "resetsAt": 1_800_100_000},
        }
    )
    current = TelegramCodexBot._codex_limits_snapshot(
        {
            "primary": {"usedPercent": 0, "resetsAt": 1_800_019_000},
            "secondary": {"usedPercent": 0, "resetsAt": 1_800_119_000},
        }
    )

    assert TelegramCodexBot._is_unscheduled_full_limits_reset(
        previous, current, now=1_800_019_000
    )


def test_scheduled_secondary_rollover_is_not_reported() -> None:
    previous = TelegramCodexBot._codex_limits_snapshot(
        {
            "primary": {"usedPercent": 80, "resetsAt": 1_800_001_000},
            "secondary": {"usedPercent": 40, "resetsAt": 1_800_019_000},
        }
    )
    current = TelegramCodexBot._codex_limits_snapshot(
        {
            "primary": {"usedPercent": 0, "resetsAt": 1_800_037_000},
            "secondary": {"usedPercent": 0, "resetsAt": 1_800_623_000},
        }
    )

    assert not TelegramCodexBot._is_unscheduled_full_limits_reset(
        previous, current, now=1_800_019_000
    )
