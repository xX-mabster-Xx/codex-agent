"""Mirror the shared transcript between participants' private bot topics."""

from __future__ import annotations

import asyncio
from html import escape
import logging
from typing import Any

from aiogram.exceptions import (
    TelegramAPIError, TelegramBadRequest, TelegramForbiddenError,
    TelegramNetworkError, TelegramRetryAfter, TelegramServerError,
)
from aiogram.types import InputRichMessage

from formatting import render_markdown, split_markdown

from topic_sharing import ShareRegistry, TopicKey


log = logging.getLogger(__name__)


class SharedDelivery:
    def __init__(self, bot: Any, shares: ShareRegistry) -> None:
        self.bot = bot
        self.shares = shares
        self.human_copies: dict[tuple[int, int], list[tuple[TopicKey, int, int]]] = {}
        self.agent_copies: dict[tuple[TopicKey, int], list[tuple[TopicKey, int]]] = {}
        self.unavailable: set[TopicKey] = set()

    async def _retry(self, operation: Any) -> Any:
        for attempt in range(2):
            try:
                return await operation()
            except TelegramRetryAfter as error:
                if attempt or error.retry_after > 30:
                    raise
                await asyncio.sleep(max(error.retry_after, 0))
            except (TelegramNetworkError, TelegramServerError):
                if attempt:
                    raise
                await asyncio.sleep(0.2)

    async def _failure(self, owner_key: TopicKey, key: TopicKey, error: TelegramAPIError) -> None:
        log.warning("Shared delivery failed key=%s: %s", key, error)
        if key == owner_key or key in self.unavailable:
            return
        missing_topic = isinstance(error, TelegramBadRequest) and any(
            phrase in str(error).casefold()
            for phrase in ("thread not found", "topic not found", "chat not found", "topic was deleted")
        )
        if not (isinstance(error, TelegramForbiddenError) or missing_topic):
            return
        self.unavailable.add(key)
        try:
            await self._retry(lambda: self.bot.send_message(
                chat_id=owner_key[0], message_thread_id=owner_key[2],
                text=f"⚠️ Общий топик пользователя {key[0]} недоступен; сообщения ему не доставляются. "
                     "Попросите его открыть бот и отправить /start. Доступ сохранён.",
                disable_notification=True,
            ))
        except TelegramAPIError as warning_error:
            log.warning("Cannot notify owner of failed shared delivery: %s", warning_error)

    def _recovered(self, key: TopicKey) -> None:
        self.unavailable.discard(key)

    def recipients(self, source_key: TopicKey) -> list[TopicKey]:
        owner_key = self.shares.resolve(source_key) or source_key
        if not self.shares.members(owner_key):
            return []
        all_keys = [owner_key, *(key for _, key in self.shares.members(owner_key))]
        return [key for key in all_keys if key != source_key]

    @staticmethod
    def _human_text_chunks(text: str, author_label: str) -> list[str]:
        header = f"👤 <b>{escape(author_label[:160])}</b>\n\n"
        # Count UTF-16 code units, as required by Telegram's message limit.
        # Leave room for the label, entities and Telegram-side normalization.
        budget = max(1, 3800 - len(header.encode("utf-16-le")) // 2)
        raw_chunks: list[str] = []
        part: list[str] = []
        used = 0
        for char in text:
            units = len(char.encode("utf-16-le")) // 2
            if used + units > budget and part:
                raw_chunks.append("".join(part))
                part = []
                used = 0
            part.append(char)
            used += units
        if part or not raw_chunks:
            raw_chunks.append("".join(part))
        return [header + escape(chunk) for chunk in raw_chunks]

    async def mirror_human(
        self, message: Any, source_key: TopicKey, author_label: str
    ) -> None:
        copies: list[tuple[TopicKey, int, int]] = []
        header = f"👤 <b>{escape(author_label[:160])}</b>"
        owner_key = self.shares.resolve(source_key) or source_key
        for key in self.recipients(source_key):
            try:
                if message.text is not None:
                    for index, chunk in enumerate(self._human_text_chunks(message.text, author_label)):
                        sent = await self._retry(lambda: self.bot.send_message(
                            chat_id=key[0], message_thread_id=key[2],
                            text=chunk, disable_notification=True,
                        ))
                        copies.append((key, sent.message_id, index))
                else:
                    await self._retry(lambda: self.bot.send_message(
                        chat_id=key[0], message_thread_id=key[2],
                        text=header, disable_notification=True,
                    ))
                    copied = await self._retry(lambda: self.bot.copy_message(
                        chat_id=key[0], message_thread_id=key[2],
                        from_chat_id=message.chat.id, message_id=message.message_id,
                        disable_notification=True,
                    ))
                    copies.append((key, copied.message_id, -1))
                self._recovered(key)
            except TelegramAPIError as error:
                await self._failure(owner_key, key, error)
        if copies:
            self.human_copies[message.chat.id, message.message_id] = copies
            if len(self.human_copies) > 10_000:
                for old_key in list(self.human_copies)[:2_000]:
                    self.human_copies.pop(old_key, None)

    async def edit_human(self, message: Any, author_label: str) -> None:
        copies = self.human_copies.get((message.chat.id, message.message_id), ())
        chunks = self._human_text_chunks(message.text, author_label)
        source_key = (message.chat.id, "forum", message.message_thread_id)
        owner_key = self.shares.resolve(source_key) or source_key
        for key, message_id, index in list(copies):
            if key != owner_key and key not in [guest for _, guest in self.shares.members(owner_key)]:
                continue
            if index < 0:
                continue
            text = chunks[index] if index < len(chunks) else "✏️ Текст сокращён в исходном сообщении."
            try:
                await self._retry(lambda: self.bot.edit_message_text(
                    text, chat_id=key[0], message_id=message_id,
                ))
                self._recovered(key)
            except TelegramAPIError as error:
                await self._failure(owner_key, key, error)
        existing_counts: dict[TopicKey, int] = {}
        for key, _, index in copies:
            if index >= 0:
                existing_counts[key] = max(existing_counts.get(key, 0), index + 1)
        for key, count in existing_counts.items():
            if key != owner_key and key not in [guest for _, guest in self.shares.members(owner_key)]:
                continue
            for index in range(count, len(chunks)):
                try:
                    sent = await self._retry(lambda: self.bot.send_message(
                        chat_id=key[0], message_thread_id=key[2],
                        text=chunks[index], disable_notification=True,
                    ))
                    copies.append((key, sent.message_id, index))
                except TelegramAPIError as error:
                    await self._failure(owner_key, key, error)
                    break

    async def broadcast_html(
        self, owner_key: TopicKey, text: str, *, silent: bool = True
    ) -> Any:
        owner_message = await self._retry(lambda: self.bot.send_message(
            chat_id=owner_key[0], message_thread_id=owner_key[2],
            text=text, disable_notification=silent,
            link_preview_options={"is_disabled": True},
        ))
        copies: list[tuple[TopicKey, int]] = []
        for _, guest_key in self.shares.members(owner_key):
            try:
                sent = await self._retry(lambda: self.bot.send_message(
                    chat_id=guest_key[0], message_thread_id=guest_key[2],
                    text=text, disable_notification=silent,
                    link_preview_options={"is_disabled": True},
                ))
                copies.append((guest_key, sent.message_id))
                self._recovered(guest_key)
            except TelegramAPIError as error:
                await self._failure(owner_key, guest_key, error)
        if copies:
            self.agent_copies[owner_key, owner_message.message_id] = copies
        return owner_message

    async def broadcast_markdown(
        self, owner_key: TopicKey, text: str, *, silent: bool = True
    ) -> list[Any]:
        owner_messages: list[Any] = []
        for chunk in split_markdown(text):
            recipients = [owner_key, *(key for _, key in self.shares.members(owner_key))]
            owner_message: Any = None
            copies: list[tuple[TopicKey, int]] = []
            for key in recipients:
                try:
                    sent = await self._retry(lambda: self.bot.send_rich_message(
                        chat_id=key[0], message_thread_id=key[2],
                        rich_message=InputRichMessage(markdown=chunk),
                        disable_notification=silent,
                    ))
                except TelegramAPIError:
                    try:
                        sent = await self._retry(lambda: self.bot.send_message(
                            chat_id=key[0], message_thread_id=key[2],
                            text=render_markdown(chunk), disable_notification=silent,
                            link_preview_options={"is_disabled": True},
                        ))
                    except TelegramAPIError as error:
                        if key == owner_key:
                            raise
                        await self._failure(owner_key, key, error)
                        continue
                self._recovered(key)
                if key == owner_key:
                    owner_message = sent
                    owner_messages.append(sent)
                else:
                    copies.append((key, sent.message_id))
            if owner_message and copies:
                self.agent_copies[owner_key, owner_message.message_id] = copies
        return owner_messages

    async def broadcast_edit(
        self, owner_key: TopicKey, owner_message_id: int, text: str
    ) -> None:
        copies = [(owner_key, owner_message_id), *self.agent_copies.get(
            (owner_key, owner_message_id), []
        )]
        active = {owner_key, *(guest for _, guest in self.shares.members(owner_key))}
        for key, message_id in copies:
            if key not in active:
                continue
            try:
                await self._retry(lambda: self.bot.edit_message_text(
                    text, chat_id=key[0], message_id=message_id,
                    link_preview_options={"is_disabled": True},
                ))
                self._recovered(key)
            except TelegramAPIError as error:
                await self._failure(owner_key, key, error)

    async def notify_approval_waiting(self, owner_key: TopicKey) -> None:
        for _, guest_key in self.shares.members(owner_key):
            try:
                await self._retry(lambda: self.bot.send_message(
                    chat_id=guest_key[0], message_thread_id=guest_key[2],
                    text="⏳ Для продолжения требуется подтверждение владельца топика.",
                    disable_notification=True,
                ))
                self._recovered(guest_key)
            except TelegramAPIError as error:
                await self._failure(owner_key, guest_key, error)

    async def notify_approval_result(self, owner_key: TopicKey, allowed: bool) -> None:
        status = "разрешил" if allowed else "отклонил"
        for _, guest_key in self.shares.members(owner_key):
            try:
                await self._retry(lambda: self.bot.send_message(
                    chat_id=guest_key[0], message_thread_id=guest_key[2],
                    text=f"Владелец {status} действие агента.",
                    disable_notification=True,
                ))
                self._recovered(guest_key)
            except TelegramAPIError as error:
                await self._failure(owner_key, guest_key, error)
