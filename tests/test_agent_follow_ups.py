from datetime import datetime, timedelta, timezone

from scheduled_jobs import MAX_AGENT_FOLLOW_UPS_PER_TOPIC, ScheduledJobStore


def test_agent_follow_up_limit_is_shared_by_bot_and_mcp() -> None:
    assert MAX_AGENT_FOLLOW_UPS_PER_TOPIC == 8


def test_list_agent_follow_ups_returns_only_agent_checkpoints(tmp_path) -> None:
    store = ScheduledJobStore(tmp_path / "jobs.db")
    due = datetime.now(timezone.utc) + timedelta(minutes=10)
    store.create(
        key=(1, "forum", 10),
        kind="task",
        text="[agent follow-up] Check build output",
        due_at=due,
    )
    store.create(
        key=(1, "forum", 11),
        kind="reminder",
        text="Call Boris",
        due_at=due,
    )

    jobs = store.list_agent_follow_ups()

    assert len(jobs) == 1
    assert jobs[0].topic_id == 10
    assert jobs[0].text == "[agent follow-up] Check build output"
