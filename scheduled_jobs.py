"""Persistent local scheduler for Telegram reminders and deferred Codex tasks."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Iterator


SCHEDULER_HOME = Path(
    os.environ.get(
        "CODEX_SCHEDULER_HOME", "/home/mabster/.local/share/telegram-codex-scheduler"
    )
).expanduser().resolve()
DATABASE_PATH = SCHEDULER_HOME / "jobs.db"
VALID_KINDS = {"reminder", "task"}
MAX_AGENT_FOLLOW_UPS_PER_TOPIC = 8


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    id: int
    chat_id: int
    topic_kind: str
    topic_id: int
    kind: str
    text: str
    due_at: datetime
    status: str
    created_at: datetime
    triggered_at: datetime | None
    detail: str | None

    @property
    def key(self) -> tuple[int, str, int]:
        return (self.chat_id, self.topic_kind, self.topic_id)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _as_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _from_text(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class ScheduledJobError(ValueError):
    pass


class ScheduledJobStore:
    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    topic_kind TEXT NOT NULL CHECK(topic_kind IN ('chat', 'forum', 'direct')),
                    topic_id INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('reminder', 'task')),
                    text TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'cancelled', 'failed')),
                    created_at TEXT NOT NULL,
                    triggered_at TEXT,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS scheduled_jobs_due_idx
                    ON scheduled_jobs(status, due_at);
                CREATE INDEX IF NOT EXISTS scheduled_jobs_destination_idx
                    ON scheduled_jobs(chat_id, topic_kind, topic_id, status, due_at);
                """
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _row(row: sqlite3.Row) -> ScheduledJob:
        return ScheduledJob(
            id=int(row["id"]),
            chat_id=int(row["chat_id"]),
            topic_kind=str(row["topic_kind"]),
            topic_id=int(row["topic_id"]),
            kind=str(row["kind"]),
            text=str(row["text"]),
            due_at=_from_text(str(row["due_at"])) or _now(),
            status=str(row["status"]),
            created_at=_from_text(str(row["created_at"])) or _now(),
            triggered_at=_from_text(row["triggered_at"]),
            detail=row["detail"],
        )

    def create(
        self,
        *,
        key: tuple[int, str, int],
        kind: str,
        text: str,
        due_at: datetime,
    ) -> ScheduledJob:
        if kind not in VALID_KINDS:
            raise ScheduledJobError("kind must be reminder or task")
        text = " ".join(text.split())
        if not text:
            raise ScheduledJobError("text must not be empty")
        if len(text) > 4_000:
            raise ScheduledJobError("text is too long (maximum 4000 characters)")
        if due_at.tzinfo is None:
            raise ScheduledJobError("due_at must include a timezone")
        due_at = due_at.astimezone(timezone.utc).replace(microsecond=0)
        if due_at <= _now():
            raise ScheduledJobError("due_at must be in the future")
        if due_at.year > _now().year + 5:
            raise ScheduledJobError("due_at is more than five years away")
        chat_id, topic_kind, topic_id = key
        if topic_kind not in {"chat", "forum", "direct"}:
            raise ScheduledJobError("invalid Telegram destination")
        now = _now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO scheduled_jobs(chat_id, topic_kind, topic_id, kind, text, due_at, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (chat_id, topic_kind, topic_id, kind, text, _as_text(due_at), _as_text(now)),
            )
            row = connection.execute(
                "SELECT * FROM scheduled_jobs WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        assert row is not None
        return self._row(row)

    def list_pending(self, key: tuple[int, str, int], *, limit: int = 30) -> list[ScheduledJob]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE chat_id = ? AND topic_kind = ? AND topic_id = ? AND status = 'pending'
                ORDER BY due_at, id LIMIT ?
                """,
                (*key, limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def list_agent_follow_ups(self, *, limit: int = 100) -> list[ScheduledJob]:
        """List every pending autonomous agent checkpoint across the user's topics."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE status = 'pending' AND text LIKE '[agent follow-up] %'
                ORDER BY due_at, id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def cancel(self, job_id: int, key: tuple[int, str, int]) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE scheduled_jobs SET status = 'cancelled', detail = 'Cancelled by user'
                WHERE id = ? AND chat_id = ? AND topic_kind = ? AND topic_id = ? AND status = 'pending'
                """,
                (job_id, *key),
            )
        return cursor.rowcount == 1

    def claim_due(self, *, limit: int = 20) -> list[ScheduledJob]:
        """Atomically hand due jobs to this bot process for delivery/dispatch."""
        now = _now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM scheduled_jobs
                WHERE status = 'pending' AND due_at <= ?
                ORDER BY due_at, id LIMIT ?
                """,
                (_as_text(now), limit),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                marks = ", ".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE scheduled_jobs SET status = 'running', triggered_at = ? WHERE id IN ({marks})",
                    (_as_text(now), *ids),
                )
                rows = connection.execute(
                    f"SELECT * FROM scheduled_jobs WHERE id IN ({marks}) ORDER BY due_at, id",
                    ids,
                ).fetchall()
        return [self._row(row) for row in rows]

    def finish(self, job_id: int, *, detail: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE scheduled_jobs SET status = 'completed', detail = ? WHERE id = ? AND status = 'running'",
                (detail[:1000], job_id),
            )

    def fail(self, job_id: int, *, detail: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE scheduled_jobs SET status = 'failed', detail = ? WHERE id = ? AND status = 'running'",
                (detail[:1000], job_id),
            )

    def recover_interrupted(self) -> int:
        """Retry jobs interrupted by a bot restart; delivery can be duplicated only on a crash mid-send."""
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE scheduled_jobs SET status = 'pending', triggered_at = NULL, detail = 'Retry after bot restart' WHERE status = 'running'"
            )
        return cursor.rowcount
