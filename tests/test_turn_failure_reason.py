from bot import TelegramCodexBot


def test_turn_completion_extracts_error_from_turn() -> None:
    assert TelegramCodexBot._turn_completion_error(
        {},
        {
            "status": "failed",
            "error": {
                "message": "Selected model is at capacity. Please try a different model."
            },
        },
    ) == "Selected model is at capacity. Please try a different model."


def test_turn_completion_extracts_top_level_error() -> None:
    assert TelegramCodexBot._turn_completion_error(
        {"error": {"message": "You've hit your usage limit."}},
        {"status": "failed"},
    ) == "You've hit your usage limit."
