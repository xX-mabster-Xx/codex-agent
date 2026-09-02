#!/usr/bin/env python3
"""Send task artifacts to the owner through the Telegram Bot API."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from fastmcp import FastMCP


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG = dotenv_values(PROJECT_ROOT / ".env")
OWNER_ID = int(str(CONFIG.get("TELEGRAM_USER_ID") or "0"))
BOT_TOKEN = str(CONFIG.get("BOT_TOKEN") or "")
PROXY_URL = str(CONFIG.get("PROXY_URL") or "").strip() or None
MAX_FILE_BYTES = 50 * 1024 * 1024
SENSITIVE_NAMES = {".env", ".netrc", "credentials", "secrets"}
SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


def _configured_roots() -> list[Path]:
    roots = [PROJECT_ROOT]
    for name in ("PROJECT_DIR", "PROJECTS_ROOT"):
        raw = str(CONFIG.get(name) or "").strip()
        if raw:
            roots.append(Path(raw).expanduser().resolve())
    workspace = Path("/home/mabster/progs/workspace")
    if workspace.is_dir():
        roots.append(workspace.resolve())
    return list(dict.fromkeys(roots))


ALLOWED_ROOTS = _configured_roots()
mcp = FastMCP(
    "telegram_bot",
    instructions=(
        "Delivers local task artifacts to the configured owner through the current "
        "Telegram bot. It does not use a personal Telegram account and has no chat "
        "or account parameter. Use it when the user asks for a file here, or when a "
        "completed task's file is clearly the requested result."
    ),
)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_file(raw_path: str) -> Path:
    if not BOT_TOKEN or not OWNER_ID:
        raise ValueError("Telegram bot delivery is not configured")
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, ValueError) as error:
        raise ValueError("file_path must be an existing file") from error
    if not path.is_file() or not any(_within(path, root) for root in ALLOWED_ROOTS):
        raise ValueError("file_path is outside the approved delivery roots")
    if path.name.casefold() in SENSITIVE_NAMES or path.suffix.casefold() in SENSITIVE_SUFFIXES:
        raise ValueError("credentials and private-key files cannot be delivered")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("file is larger than the 50 MB delivery limit")
    return path


def _topic_fields(topic_kind: str, topic_id: int) -> dict[str, str]:
    """Convert the current bot-session destination into Bot API parameters."""
    kind = str(topic_kind or "").casefold()
    if kind == "chat":
        if topic_id != 0:
            raise ValueError("topic_id must be 0 for an ordinary chat")
        return {}
    if kind not in {"forum", "direct"} or isinstance(topic_id, bool) or topic_id <= 0:
        raise ValueError("topic_kind/topic_id must describe the current Telegram destination")
    field = "message_thread_id" if kind == "forum" else "direct_messages_topic_id"
    return {field: str(topic_id)}


@mcp.tool(
    name="send_file_to_user",
    title="Send file through this Telegram bot",
    description=(
        "Send one local file directly to the configured user through this Telegram "
        "bot. The recipient is fixed; use the current topic_kind and topic_id from "
        "the Telegram destination context so the file arrives in this topic."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def send_file_to_user(
    file_path: str,
    topic_kind: str,
    topic_id: int,
    caption: str | None = None,
) -> dict[str, Any]:
    """Upload an allowed file through Bot API to the owner configured in .env."""
    try:
        path = _safe_file(file_path)
        destination = _topic_fields(topic_kind, topic_id)
        clean_caption = " ".join(str(caption or "").split())[:1024]
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
        async with httpx.AsyncClient(proxy=PROXY_URL, timeout=120) as client:
            with path.open("rb") as source:
                response = await client.post(
                    url,
                    data={
                        "chat_id": str(OWNER_ID),
                        "caption": clean_caption,
                        **destination,
                    },
                    files={"document": (path.name, source, mime_type)},
                )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            return {"ok": False, "error": "Telegram Bot API rejected the document"}
        result = payload.get("result") or {}
        return {
            "ok": True,
            "file_name": path.name,
            "message_id": result.get("message_id"),
            "topic_kind": topic_kind,
            "topic_id": topic_id,
        }
    except (OSError, ValueError, httpx.HTTPError) as error:
        return {"ok": False, "error": str(error)[:500]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
