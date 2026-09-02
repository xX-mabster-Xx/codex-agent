#!/usr/bin/env python3
"""Dedicated Telegram approval bot for root MCP requests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html import escape
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import dotenv_values


SOCKET_PATH = "/run/codex-sudo/daemon.sock"
MAX_RESPONSE_BYTES = 140_000
POLL_INTERVAL_SECONDS = 1.0
log = logging.getLogger("root_approval_bot")


@dataclass(frozen=True, slots=True)
class ApprovalBotConfig:
    token: str
    user_id: int
    proxy_url: str | None

    @classmethod
    def load(cls) -> "ApprovalBotConfig":
        values = {
            **dotenv_values(Path(__file__).with_name(".env")),
            **os.environ,
        }

        def value(name: str, default: str = "") -> str:
            return str(values.get(name) or default).strip()

        token = value("ROOT_APPROVAL_BOT_TOKEN")
        user_id = value("ROOT_APPROVAL_TELEGRAM_USER_ID", value("TELEGRAM_USER_ID"))
        proxy_url = value("PROXY_URL", "socks5://127.0.0.1:2060") or None
        if not token:
            raise RuntimeError("ROOT_APPROVAL_BOT_TOKEN is missing in .env")
        if not user_id.isdecimal():
            raise RuntimeError("ROOT_APPROVAL_TELEGRAM_USER_ID must be an integer")
        if proxy_url:
            parsed = urlparse(proxy_url)
            try:
                port = parsed.port
            except ValueError as error:
                raise RuntimeError("PROXY_URL contains an invalid port") from error
            if parsed.scheme not in {"http", "https", "socks4", "socks5"}:
                raise RuntimeError("PROXY_URL has an unsupported scheme")
            if not parsed.hostname or port is None:
                raise RuntimeError("PROXY_URL must contain a host and port")
        return cls(token=token, user_id=int(user_id), proxy_url=proxy_url)


async def daemon_call(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(SOCKET_PATH), timeout=5
        )
    except OSError as error:
        return {"ok": False, "error": f"Root executor is unavailable: {error}"}
    try:
        writer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=10)
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            return {"ok": False, "error": "Root executor returned no valid response"}
        response = json.loads(raw)
        return response if isinstance(response, dict) else {"ok": False, "error": "Invalid response"}
    except (OSError, TimeoutError, json.JSONDecodeError) as error:
        return {"ok": False, "error": f"Root executor communication failed: {error}"}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def approval_text(request: dict[str, Any]) -> str:
    return (
        "⚠️ <b>Запрошена root-команда</b>\n\n"
        f"<b>Команда</b>\n<pre>{escape(str(request['command']))}</pre>\n"
        f"<b>Каталог</b>\n<code>{escape(str(request['cwd']))}</code>\n"
        f"<b>Лимит</b>: {int(request['timeout_seconds'])} сек.\n\n"
        f"<b>Зачем</b>\n{escape(str(request['reason']))}\n\n"
        f"<b>Ожидаемый результат</b>\n{escape(str(request['expected_result']))}\n\n"
        f"<b>Риски</b>\n{escape(str(request['risks']))}\n\n"
        "Команда будет выполнена от <code>root</code> только после вашего решения."
    )


def approval_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять", callback_data=f"root:approve:{request_id}"
                ),
                InlineKeyboardButton(
                    text="🚫 Отклонить", callback_data=f"root:reject:{request_id}"
                ),
            ]
        ]
    )


class RootApprovalBot:
    def __init__(self, config: ApprovalBotConfig) -> None:
        self.config = config
        self.bot = Bot(
            config.token,
            session=AiohttpSession(proxy=config.proxy_url),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher()
        self.router = Router()
        self.router.callback_query.register(
            self.on_decision, F.data.startswith("root:")
        )
        self.dp.include_router(self.router)
        self.notified: dict[str, int] = {}
        self._closed = asyncio.Event()

    async def run(self) -> None:
        poller = asyncio.create_task(self._publish_pending(), name="root-approval-poller")
        try:
            await self.dp.start_polling(self.bot, allowed_updates=self.dp.resolve_used_update_types())
        finally:
            self._closed.set()
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
            await self.bot.session.close()

    async def _publish_pending(self) -> None:
        while not self._closed.is_set():
            response = await daemon_call({"action": "list_pending"})
            if response.get("ok"):
                requests = response.get("requests")
                if isinstance(requests, list):
                    current_ids: set[str] = set()
                    for request in requests:
                        if not isinstance(request, dict):
                            continue
                        request_id = request.get("request_id")
                        if not isinstance(request_id, str):
                            continue
                        current_ids.add(request_id)
                        if request_id in self.notified:
                            continue
                        try:
                            message = await self.bot.send_message(
                                self.config.user_id,
                                approval_text(request),
                                reply_markup=approval_keyboard(request_id),
                                link_preview_options={"is_disabled": True},
                            )
                        except TelegramAPIError:
                            log.exception("Could not send root approval request id=%s", request_id)
                            continue
                        self.notified[request_id] = message.message_id
                        log.info("Published root approval request id=%s", request_id)
                    self.notified = {
                        request_id: message_id
                        for request_id, message_id in self.notified.items()
                        if request_id in current_ids
                    }
            else:
                log.warning("Could not list pending root approvals: %s", response.get("error"))
            try:
                await asyncio.wait_for(self._closed.wait(), timeout=POLL_INTERVAL_SECONDS)
            except TimeoutError:
                pass

    async def on_decision(self, callback: CallbackQuery) -> None:
        if callback.from_user.id != self.config.user_id or not callback.data:
            await callback.answer("Недоступно", show_alert=True)
            return
        try:
            _, decision, request_id = callback.data.split(":", 2)
        except ValueError:
            await callback.answer("Некорректная кнопка", show_alert=True)
            return
        if decision not in {"approve", "reject"}:
            await callback.answer("Некорректное решение", show_alert=True)
            return
        response = await daemon_call(
            {"action": "decide", "request_id": request_id, "decision": decision}
        )
        if not response.get("ok"):
            await callback.answer(
                str(response.get("error", "Запрос уже обработан или устарел")),
                show_alert=True,
            )
            return
        if callback.message:
            status = "✅ <b>Одобрено.</b> Команда выполняется." if decision == "approve" else "🚫 <b>Отклонено.</b> Команда не будет выполнена."
            try:
                await callback.message.edit_text(
                    callback.message.html_text + "\n\n" + status,
                    reply_markup=None,
                    link_preview_options={"is_disabled": True},
                )
            except TelegramAPIError:
                await callback.message.edit_reply_markup(reply_markup=None)
        self.notified.pop(request_id, None)
        await callback.answer("Принято" if decision == "approve" else "Отклонено")


async def main() -> None:
    config = ApprovalBotConfig.load()
    await RootApprovalBot(config).run()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
