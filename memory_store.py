"""Local shared memory storage for Telegram Codex agents.

The database is deliberately outside agent-writable project directories.  The
MCP server exposes search and controlled writes, while the Telegram frontend
only injects compact, explicitly pinned knowledge into agent prompts.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator


MEMORY_HOME = Path(
    os.environ.get(
        "CODEX_MEMORY_HOME", "/home/mabster/.local/share/telegram-codex-memory"
    )
).expanduser().resolve()
DATABASE_PATH = MEMORY_HOME / "memory.db"
VALID_KINDS = {"user_rule", "knowledge", "note"}
VALID_SCOPES = {"global", "project", "topic"}
KIND_ALIASES = {
    "rule": "user_rule",
    "instruction": "user_rule",
    "preference": "knowledge",
    "fact": "knowledge",
    "decision": "knowledge",
    "reference": "knowledge",
    "reminder": "note",
    "observation": "note",
    "task": "note",
}
SCOPE_ALIASES = {
    "personal": "global",
    "user": "global",
    "shared": "global",
    "all": "global",
}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:sk|rk|ghp|gho|xox[baprs])-[A-Za-z0-9_\-]{16,}\b", re.I),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|bot[_-]?token|"
        r"client[_-]?secret|password)\b\s*[:=]\s*[^\s]{8,}",
        re.I,
    ),
)


class MemoryError(ValueError):
    """An input is invalid or unsafe to store in shared memory."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_text(value: str, *, field: str, maximum: int) -> str:
    value = " ".join(str(value or "").split())
    if not value:
        raise MemoryError(f"{field} must not be empty")
    if len(value) > maximum:
        raise MemoryError(f"{field} is too long (maximum {maximum} characters)")
    return value


def _contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


class MemoryStore:
    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('user_rule', 'knowledge', 'note')),
                    scope TEXT NOT NULL CHECK(scope IN ('global', 'project', 'topic')),
                    project TEXT,
                    topic TEXT,
                    tags TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'verified' CHECK(status IN ('verified', 'untrusted', 'archived')),
                    pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0, 1)),
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS memories_scope_idx
                    ON memories(scope, project, topic, status, pinned);
                CREATE INDEX IF NOT EXISTS memories_expiry_idx
                    ON memories(expires_at);
                CREATE TABLE IF NOT EXISTS memory_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(memory_id) REFERENCES memories(id)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    title, content, tags,
                    content='memories', content_rowid='id', tokenize='unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, title, content, tags)
                    VALUES (new.id, new.title, new.content, new.tags);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
                    VALUES ('delete', old.id, old.title, old.content, old.tags);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
                    VALUES ('delete', old.id, old.title, old.content, old.tags);
                    INSERT INTO memories_fts(rowid, title, content, tags)
                    VALUES (new.id, new.title, new.content, new.tags);
                END;
                """
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _row(row: sqlite3.Row, *, content_limit: int | None = None) -> dict[str, Any]:
        content = str(row["content"])
        if content_limit and len(content) > content_limit:
            content = content[:content_limit].rstrip() + "…"
        return {
            "id": int(row["id"]),
            "title": str(row["title"]),
            "content": content,
            "kind": str(row["kind"]),
            "scope": str(row["scope"]),
            "project": row["project"],
            "topic": row["topic"],
            "tags": json.loads(row["tags"]),
            "source": str(row["source"]),
            "status": str(row["status"]),
            "pinned": bool(row["pinned"]),
            "expires_at": row["expires_at"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _validate_scope(scope: str, project: str | None, topic: str | None) -> tuple[str, str | None, str | None]:
        scope = SCOPE_ALIASES.get(str(scope).casefold(), str(scope).casefold())
        if scope not in VALID_SCOPES:
            raise MemoryError(f"scope must be one of: {', '.join(sorted(VALID_SCOPES))}")
        project = str(project).strip() if project else None
        topic = str(topic).strip() if topic else None
        if scope == "project" and not project:
            raise MemoryError("project scope requires project")
        if scope == "topic" and (not project or not topic):
            raise MemoryError("topic scope requires both project and topic")
        if project and len(project) > 500:
            raise MemoryError("project is too long")
        if topic and len(topic) > 250:
            raise MemoryError("topic is too long")
        return scope, project, topic

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        result: list[str] = []
        for tag in tags or []:
            normalized = _validate_text(str(tag), field="tag", maximum=64).casefold()
            if normalized not in result:
                result.append(normalized)
        if len(result) > 20:
            raise MemoryError("no more than 20 tags are allowed")
        return result

    def remember(
        self,
        *,
        title: str,
        content: str,
        kind: str,
        scope: str,
        project: str | None,
        topic: str | None,
        tags: list[str] | None,
        source: str,
        pinned: bool,
        expires_in_days: int | None,
        actor: str = "mcp-agent",
    ) -> dict[str, Any]:
        title = _validate_text(title, field="title", maximum=240)
        content = _validate_text(content, field="content", maximum=16_000)
        source = _validate_text(source, field="source", maximum=240)
        requested_kind = str(kind).casefold()
        kind = KIND_ALIASES.get(requested_kind, requested_kind)
        if kind not in VALID_KINDS:
            # Kind is descriptive metadata. Unknown labels should not prevent a
            # user-confirmed fact from being retained; preserve it as a note.
            kind = "note"
        if _contains_secret(title) or _contains_secret(content):
            raise MemoryError("memory must not contain passwords, tokens, API keys, or private keys")
        scope, project, topic = self._validate_scope(scope, project, topic)
        tags_json = json.dumps(self._normalize_tags(tags), ensure_ascii=False)
        if expires_in_days is not None and not 1 <= expires_in_days <= 3650:
            raise MemoryError("expires_in_days must be between 1 and 3650")
        if kind == "user_rule":
            pinned = True
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat(timespec="seconds")
            if expires_in_days is not None
            else None
        )
        now = _now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memories(title, content, kind, scope, project, topic, tags, source, pinned, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (title, content, kind, scope, project, topic, tags_json, source, int(pinned), expires_at, now, now),
            )
            memory_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO memory_audit(memory_id, action, actor, details, created_at) VALUES (?, ?, ?, ?, ?)",
                (memory_id, "create", actor, json.dumps({"pinned": bool(pinned)}, ensure_ascii=False), now),
            )
            row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        assert row is not None
        return self._row(row)

    def search(
        self,
        query: str,
        *,
        scope: str | None = None,
        project: str | None = None,
        topic: str | None = None,
        include_untrusted: bool = False,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = _validate_text(query, field="query", maximum=500)
        if not 1 <= limit <= 30:
            raise MemoryError("limit must be between 1 and 30")
        terms = re.findall(r"[\w-]{2,}", query, flags=re.UNICODE)
        if not terms:
            raise MemoryError("query must contain at least one word with two characters")
        match = " AND ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
        filters = ["(m.expires_at IS NULL OR m.expires_at > ?)"]
        params: list[Any] = [_now()]
        if not include_untrusted:
            filters.append("m.status = 'verified'")
        else:
            filters.append("m.status != 'archived'")
        if scope:
            if scope not in VALID_SCOPES:
                raise MemoryError(f"scope must be one of: {', '.join(sorted(VALID_SCOPES))}")
            filters.append("m.scope = ?")
            params.append(scope)
        if project:
            filters.append("m.project = ?")
            params.append(project)
        if topic:
            filters.append("m.topic = ?")
            params.append(topic)
        params.extend([match, limit])
        sql = (
            "SELECT m.*, bm25(memories_fts) AS relevance FROM memories_fts "
            "JOIN memories m ON m.id = memories_fts.rowid WHERE "
            + " AND ".join(filters)
            + " AND memories_fts MATCH ? ORDER BY relevance LIMIT ?"
        )
        with self._connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        results = [self._row(row, content_limit=1800) for row in rows]
        for result, row in zip(results, rows):
            result["relevance"] = round(float(row["relevance"]), 3)
        return results

    def get(self, memory_id: int, *, include_untrusted: bool = False) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ? AND (expires_at IS NULL OR expires_at > ?)",
                (memory_id, _now()),
            ).fetchone()
        if row is None or (not include_untrusted and row["status"] != "verified"):
            return None
        return self._row(row)

    def list_memories(
        self, *, scope: str | None = None, kind: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise MemoryError("limit must be between 1 and 100")
        filters = ["status = 'verified'", "(expires_at IS NULL OR expires_at > ?)"]
        params: list[Any] = [_now()]
        if scope:
            if scope not in VALID_SCOPES:
                raise MemoryError(f"scope must be one of: {', '.join(sorted(VALID_SCOPES))}")
            filters.append("scope = ?")
            params.append(scope)
        if kind:
            if kind not in VALID_KINDS:
                raise MemoryError(f"kind must be one of: {', '.join(sorted(VALID_KINDS))}")
            filters.append("kind = ?")
            params.append(kind)
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM memories WHERE " + " AND ".join(filters) + " ORDER BY pinned DESC, updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row(row, content_limit=1200) for row in rows]

    def update(
        self,
        memory_id: int,
        *,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        pinned: bool | None = None,
        expires_in_days: int | None = None,
        actor: str = "mcp-agent",
    ) -> dict[str, Any]:
        with self._connection() as connection:
            current = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if current is None or current["status"] == "archived":
                raise MemoryError("memory was not found")
            changes: dict[str, Any] = {}
            if title is not None:
                changes["title"] = _validate_text(title, field="title", maximum=240)
            if content is not None:
                changes["content"] = _validate_text(content, field="content", maximum=16_000)
            if any(_contains_secret(str(value)) for value in changes.values()):
                raise MemoryError("memory must not contain passwords, tokens, API keys, or private keys")
            if tags is not None:
                changes["tags"] = json.dumps(self._normalize_tags(tags), ensure_ascii=False)
            if pinned is not None:
                changes["pinned"] = int(pinned or current["kind"] == "user_rule")
            if expires_in_days is not None:
                if not 1 <= expires_in_days <= 3650:
                    raise MemoryError("expires_in_days must be between 1 and 3650")
                changes["expires_at"] = (
                    datetime.now(timezone.utc) + timedelta(days=expires_in_days)
                ).isoformat(timespec="seconds")
            if not changes:
                raise MemoryError("provide at least one field to update")
            changes["updated_at"] = _now()
            assignments = ", ".join(f"{name} = ?" for name in changes)
            connection.execute(
                f"UPDATE memories SET {assignments} WHERE id = ?",
                [*changes.values(), memory_id],
            )
            connection.execute(
                "INSERT INTO memory_audit(memory_id, action, actor, details, created_at) VALUES (?, ?, ?, ?, ?)",
                (memory_id, "update", actor, json.dumps(sorted(changes), ensure_ascii=False), _now()),
            )
            row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        assert row is not None
        return self._row(row)

    def archive(self, memory_id: int, *, actor: str = "mcp-agent") -> bool:
        with self._connection() as connection:
            current = connection.execute("SELECT id FROM memories WHERE id = ? AND status != 'archived'", (memory_id,)).fetchone()
            if current is None:
                return False
            now = _now()
            connection.execute("UPDATE memories SET status = 'archived', pinned = 0, updated_at = ? WHERE id = ?", (now, memory_id))
            connection.execute(
                "INSERT INTO memory_audit(memory_id, action, actor, details, created_at) VALUES (?, ?, ?, ?, ?)",
                (memory_id, "archive", actor, "{}", now),
            )
        return True

    def prompt_context(self, *, maximum_characters: int = 6000) -> str:
        """Return only pinned verified records for developer instructions."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memories
                WHERE status = 'verified' AND pinned = 1
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY CASE kind WHEN 'user_rule' THEN 0 WHEN 'knowledge' THEN 1 ELSE 2 END,
                         updated_at DESC
                """,
                (_now(),),
            ).fetchall()
        parts: list[str] = []
        used = 0
        for row in rows:
            entry = f"- [{row['kind']}] {row['title']}: {row['content']}"
            if used + len(entry) + 1 > maximum_characters:
                break
            parts.append(entry)
            used += len(entry) + 1
        return "\n".join(parts)

    def status(self) -> dict[str, Any]:
        with self._connection() as connection:
            counts = connection.execute(
                "SELECT kind, status, COUNT(*) AS count FROM memories GROUP BY kind, status"
            ).fetchall()
            pinned = connection.execute(
                "SELECT COUNT(*) FROM memories WHERE pinned = 1 AND status = 'verified' AND (expires_at IS NULL OR expires_at > ?)",
                (_now(),),
            ).fetchone()[0]
        return {
            "database": str(self.path),
            "pinned_for_prompt": int(pinned),
            "counts": {f"{row['kind']}:{row['status']}": int(row["count"]) for row in counts},
        }
