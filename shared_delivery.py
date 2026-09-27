"""Mirror the shared transcript between participants' private bot topics."""

from __future__ import annotations

from html import escape
import logging
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputRichMessage

from formatting import render_markdown, split_markdown

from topic_sharing import ShareRegistry, TopicKey


log = logging.getLogger(__name__)


class SharedDelivery:
    def __init__(self, bot: Any, shares: ShareRegistry) -> None:
        self.bot = bot
        self.shares = shares
        self.human_copies: dict[tuple[int, int], list[tuple[TopicKey, int]]] = {}
        self.agent_copies: dict[tuple[TopicKey, int], list[tuple[TopicKey, int]]] = {}

    def recipients(self, source_key: TopicKey) -> list[TopicKey]:
        owner_key = self.shares.resolve(source_key) or source_key
        if not self.shares.members(owner_key):
            return []
        all_keys = [owner_key, *(key for _, key in self.shares.members(owner_key))]
        return [key for key in all_keys if key != source_key]

    async def mirror_human(
        self, message: Any, source_key: TopicKey, author_label: str
    ) -> None:
        copies: list[tuple[TopicKey, int]] = []
        header = f"👤 <b>{escape(author_label[:160])}</b>"
        for key in self.recipients(source_key):
            try:
                if message.text is not None:
                    sent = await self.bot.send_message(
                        chat_id=key[0], message_thread_id=key[2],
                        text=f"{header}\n\n{escape(message.text)}",
                        disable_notification=True,
                    )
                    copies.append((key, sent.message_id))
                else:
                    await self.bot.send_message(
                        chat_id=key[0], message_thread_id=key[2],
                        text=header, disable_notification=True,
                    )
                    copied = await self.bot.copy_message(
                        chat_id=key[0], message_thread_id=key[2],
                        from_chat_id=message.chat.id, message_id=message.message_id,
                        disable_notification=True,
                    )
                    copies.append((key, copied.message_id))
            except TelegramAPIError as error:
                log.warning("Cannot mirror human message to %s: %s", key, error)
        if copies:
            self.human_copies[message.chat.id, message.message_id] = copies
            if len(self.human_copies) > 10_000:
                for old_key in list(self.human_copies)[:2_000]:
                    self.human_copies.pop(old_key, None)

    async def broadcast_html(
        self, owner_key: TopicKey, text: str, *, silent: bool = True
    ) -> Any:
        owner_message = await self.bot.send_message(
            chat_id=owner_key[0], message_thread_id=owner_key[2],
            text=text, disable_notification=silent,
            link_preview_options={"is_disabled": True},
        )
        copies: list[tuple[TopicKey, int]] = []
        for _, guest_key in self.shares.members(owner_key):
            try:
                sent = await self.bot.send_message(
                    chat_id=guest_key[0], message_thread_id=guest_key[2],
                    text=text, disable_notification=silent,
                    link_preview_options={"is_disabled": True},
                )
                copies.append((guest_key, sent.message_id))
            except TelegramAPIError as error:
                log.warning("Cannot broadcast to shared topic %s: %s", guest_key, error)
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
                    sent = await self.bot.send_rich_message(
                        chat_id=key[0], message_thread_id=key[2],
                        rich_message=InputRichMessage(markdown=chunk),
                        disable_notification=silent,
                    )
                except TelegramAPIError:
                    try:
                        sent = await self.bot.send_message(
                            chat_id=key[0], message_thread_id=key[2],
                            text=render_markdown(chunk), disable_notification=silent,
                            link_preview_options={"is_disabled": True},
                        )
                    except TelegramAPIError as error:
                        if key == owner_key:
                            raise
                        log.warning("Cannot broadcast markdown to %s: %s", key, error)
                        continue
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
        for key, message_id in copies:
            try:
                await self.bot.edit_message_text(
                    text, chat_id=key[0], message_id=message_id,
                    link_preview_options={"is_disabled": True},
                )
            except TelegramAPIError as error:
                log.warning("Cannot edit shared message key=%s id=%s: %s", key, message_id, error)

    async def notify_approval_waiting(self, owner_key: TopicKey) -> None:
        for _, guest_key in self.shares.members(owner_key):
            try:
                await self.bot.send_message(
                    chat_id=guest_key[0], message_thread_id=guest_key[2],
                    text="⏳ Для продолжения требуется подтверждение владельца топика.",
                    disable_notification=True,
                )
            except TelegramAPIError as error:
                log.warning("Cannot notify guest of approval key=%s: %s", guest_key, error)

    async def notify_approval_result(self, owner_key: TopicKey, allowed: bool) -> None:
        status = "разрешил" if allowed else "отклонил"
        for _, guest_key in self.shares.members(owner_key):
            try:
                await self.bot.send_message(
                    chat_id=guest_key[0], message_thread_id=guest_key[2],
                    text=f"Владелец {status} действие агента.",
                    disable_notification=True,
                )
            except TelegramAPIError as error:
                log.warning("Cannot notify guest of approval result key=%s: %s", guest_key, error)
