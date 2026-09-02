#!/usr/bin/env python3
"""MCP interface for the Telegram bot's persistent reminder scheduler."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastmcp import FastMCP

from scheduled_jobs import (
    MAX_AGENT_FOLLOW_UPS_PER_TOPIC,
    ScheduledJob,
    ScheduledJobError,
    ScheduledJobStore,
)


store = ScheduledJobStore()
mcp = FastMCP(
    "scheduler",
    instructions=(
        "Creates persistent Telegram reminders and deferred Codex tasks for the "
        "local Telegram bot. create_scheduled_job is only for a direct user request. "
        "create_agent_follow_up is for an agent's short delayed checkpoint during an "
        "active user-requested task, such as checking a still-running build. Use the "
        "exact current Telegram destination supplied in developer instructions; do "
        "not invent or change destination IDs. A reminder sends a message, while a "
        "task queues a Codex turn and still follows ordinary access and approval rules "
        "at execution time."
    ),
)


def _job(job: ScheduledJob) -> dict[str, Any]:
    result = asdict(job)
    result["due_at"] = job.due_at.isoformat()
    result["created_at"] = job.created_at.isoformat()
    result["triggered_at"] = job.triggered_at.isoformat() if job.triggered_at else None
    return result


def _parse_due_at(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00"))
    except ValueError as error:
        raise ScheduledJobError("due_at must be an ISO-8601 timestamp with timezone") from error
    if result.tzinfo is None:
        raise ScheduledJobError("due_at must include a timezone, for example +03:00")
    return result


@mcp.tool(
    name="create_scheduled_job",
    title="Create reminder or deferred task",
    description=(
        "Create a persistent Telegram reminder or a deferred Codex task. Use only "
        "for a direct user request and only with the current chat/topic destination "
        "from the developer instructions. due_at must be ISO-8601 with a timezone."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def create_scheduled_job(
    kind: str,
    text: str,
    due_at: str,
    chat_id: int,
    topic_kind: str,
    topic_id: int,
) -> dict[str, Any]:
    """Store a user-requested job. task execution is not pre-approved."""
    try:
        job = store.create(
            key=(chat_id, topic_kind, topic_id),
            kind=kind,
            text=text,
            due_at=_parse_due_at(due_at),
        )
        return {"ok": True, "job": _job(job)}
    except ScheduledJobError as error:
        return {"ok": False, "error": str(error)}


@mcp.tool(
    name="create_agent_follow_up",
    title="Schedule agent follow-up",
    description=(
        "Schedule one short, autonomous follow-up for the active user-requested "
        "task in the current chat/topic. Use it for a concrete delayed check such as "
        "a build, deploy, download, or test that is still running. At the due time "
        "the same Codex thread receives the "
        "follow-up prompt and normal approvals still apply."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def create_agent_follow_up(
    text: str,
    due_at: str,
    chat_id: int,
    topic_kind: str,
    topic_id: int,
) -> dict[str, Any]:
    """Persist a bounded agent-created checkpoint as a normal deferred task."""
    try:
        due = _parse_due_at(due_at)
        key = (chat_id, topic_kind, topic_id)
        pending = store.list_pending(key, limit=100)
        agent_pending = sum(
            job.text.startswith("[agent follow-up]") for job in pending
        )
        if agent_pending >= MAX_AGENT_FOLLOW_UPS_PER_TOPIC:
            raise ScheduledJobError("too many pending agent follow-ups in this topic")
        job = store.create(
            key=key,
            kind="task",
            text=f"[agent follow-up] {text}",
            due_at=due,
        )
        return {"ok": True, "job": _job(job)}
    except ScheduledJobError as error:
        return {"ok": False, "error": str(error)}


@mcp.tool(
    name="list_scheduled_jobs",
    title="List scheduled reminders and tasks",
    description="List pending reminders and deferred tasks for the current chat/topic.",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def list_scheduled_jobs(chat_id: int, topic_kind: str, topic_id: int) -> dict[str, Any]:
    """Read pending jobs for exactly one Telegram destination."""
    return {
        "ok": True,
        "jobs": [_job(job) for job in store.list_pending((chat_id, topic_kind, topic_id))],
    }


@mcp.tool(
    name="cancel_scheduled_job",
    title="Cancel scheduled reminder or task",
    description=(
        "Cancel a still-pending job in the current chat/topic. This does not cancel "
        "a task that was already dispatched to Codex."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def cancel_scheduled_job(
    job_id: int, chat_id: int, topic_kind: str, topic_id: int
) -> dict[str, Any]:
    """Cancel one pending job scoped to the current Telegram destination."""
    return {
        "ok": True,
        "cancelled": store.cancel(job_id, (chat_id, topic_kind, topic_id)),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
