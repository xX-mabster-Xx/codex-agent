"""Mirror the shared transcript between participants' private bot topics."""

from __future__ import annotations

from html import escape
import logging
from typing import Any

from aiogram.exceptions import TelegramAPIError

from topic_sharing import ShareRegistry, TopicKey


log = logging.getLogger(__name__)


class SharedDelivery:
    def __init__(self, bot: Any, shares: ShareRegistry) -> None:
        self.bot = bot
        self.shares = shares
        self.human_copies: dict[tuple[int, int], list[tuple[TopicKey, int]]] = {}

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
