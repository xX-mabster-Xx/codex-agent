"""Durable invitations and membership for private Telegram bot topics."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any


TopicKey = tuple[int, str, int]
LINK_LIFETIME_SECONDS = 7 * 24 * 60 * 60


def _read_key(value: object) -> TopicKey | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    chat_id, kind, topic_id = value
    if (
        not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or chat_id <= 0
        or kind != "forum"
        or not isinstance(topic_id, int)
        or isinstance(topic_id, bool)
        or topic_id <= 0
    ):
        return None
    return chat_id, kind, topic_id


def _dict_section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name)
    return value if isinstance(value, dict) else {}


def _list_section(raw: dict[str, Any], name: str) -> list[Any]:
    value = raw.get(name)
    return value if isinstance(value, list) else []


class ShareRegistry:
    """The only source of truth for shared-topic access and pending invites."""

    def __init__(self, path: Path, owner_id: int) -> None:
        self.path = path
        self.owner_id = owner_id
        self.users: dict[int, dict[str, Any]] = {}
        self.links: dict[str, tuple[TopicKey, float]] = {}
        self.pending: dict[int, set[TopicKey]] = {}
        self.selectors: dict[int, TopicKey] = {}
        self._members: dict[tuple[TopicKey, int], TopicKey] = {}
        self._guest_to_owner: dict[TopicKey, TopicKey] = {}
        self._load()

    def _owner_key(self, key: TopicKey) -> None:
        if _read_key(list(key)) != key or key[0] != self.owner_id:
            raise ValueError("share source must be an owner's private forum topic")

    def _guest_id(self, user_id: int) -> None:
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            raise ValueError("invalid Telegram user ID")
        if user_id == self.owner_id:
            raise ValueError("owner cannot be invited as a guest")

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for raw_id, value in _dict_section(raw, "users").items():
            try:
                user_id = int(raw_id)
                self._guest_id(user_id)
                if not isinstance(value, dict):
                    continue
                self.users[user_id] = {
                    "username": self._normalize_username(value.get("username")),
                    "display_name": str(value.get("display_name") or "")[:256],
                    "private_started": value.get("private_started") is True,
                }
            except (TypeError, ValueError):
                continue
        for digest, value in _dict_section(raw, "links").items():
            if not isinstance(digest, str) or len(digest) != 64 or not isinstance(value, dict):
                continue
            key = _read_key(value.get("owner"))
            expires = value.get("expires")
            if key and key[0] == self.owner_id and isinstance(expires, (int, float)):
                self.links[digest] = key, float(expires)
        for raw_id, values in _dict_section(raw, "pending").items():
            try:
                user_id = int(raw_id)
                self._guest_id(user_id)
            except (TypeError, ValueError):
                continue
            if not isinstance(values, list):
                continue
            keys = {
                key for value in values
                if (key := _read_key(value)) and key[0] == self.owner_id
            }
            if keys:
                self.pending[user_id] = keys
        for raw_id, value in _dict_section(raw, "selectors").items():
            try:
                request_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            key = _read_key(value)
            if key and key[0] == self.owner_id and -(2**31) <= request_id < 2**31:
                self.selectors[request_id] = key
        for value in _list_section(raw, "members"):
            if not isinstance(value, dict):
                continue
            key = _read_key(value.get("owner"))
            guest = _read_key(value.get("guest"))
            user_id = value.get("user_id")
            if (
                key and key[0] == self.owner_id
                and guest and isinstance(user_id, int)
                and not isinstance(user_id, bool)
                and user_id != self.owner_id and user_id > 0
                and guest[0] == user_id
                and guest not in self._guest_to_owner
                and (key, user_id) not in self._members
            ):
                self._members[key, user_id] = guest
                self._guest_to_owner[guest] = key

    def _save(self) -> None:
        data = {
            "users": {str(user_id): value for user_id, value in self.users.items()},
            "links": {
                digest: {"owner": list(key), "expires": expires}
                for digest, (key, expires) in self.links.items()
            },
            "pending": {
                str(user_id): [list(key) for key in sorted(keys)]
                for user_id, keys in self.pending.items() if keys
            },
            "selectors": {
                str(request_id): list(key)
                for request_id, key in self.selectors.items()
            },
            "members": [
                {"owner": list(key), "user_id": user_id, "guest": list(guest)}
                for (key, user_id), guest in self._members.items()
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)

    @staticmethod
    def _normalize_username(username: object) -> str | None:
        if not isinstance(username, str):
            return None
        normalized = username.strip().lstrip("@").casefold()
        return normalized or None

    def record_user(
        self, user_id: int, username: str | None, display_name: str,
        private_started: bool,
    ) -> None:
        self._guest_id(user_id)
        previous = self.users.get(user_id, {})
        self.users[user_id] = {
            "username": self._normalize_username(username),
            "display_name": display_name[:256],
            "private_started": bool(private_started or previous.get("private_started")),
        }
        self._save()

    def known_user_id(self, username: str) -> int | None:
        normalized = self._normalize_username(username)
        if not normalized:
            return None
        matches = [
            user_id for user_id, value in self.users.items()
            if value["username"] == normalized and value["private_started"]
        ]
        return matches[0] if len(matches) == 1 else None

    def create_link(self, owner_key: TopicKey, now: float) -> str:
        self._owner_key(owner_key)
        token = secrets.token_urlsafe(24)
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.links[digest] = owner_key, now + LINK_LIFETIME_SECONDS
        self._save()
        return token

    def claim_link(self, token: str, user_id: int, now: float) -> TopicKey | None:
        self._guest_id(user_id)
        digest = hashlib.sha256(token.encode()).hexdigest()
        link = self.links.pop(digest, None)
        if link is None:
            return None
        key, expires = link
        if now >= expires:
            self._save()
            return None
        if (key, user_id) not in self._members:
            self.pending.setdefault(user_id, set()).add(key)
        self._save()
        return key

    def invite_user(self, owner_key: TopicKey, user_id: int) -> None:
        self._owner_key(owner_key)
        self._guest_id(user_id)
        if (owner_key, user_id) not in self._members:
            self.pending.setdefault(user_id, set()).add(owner_key)
            self._save()

    def pending_for(self, user_id: int) -> list[TopicKey]:
        return sorted(self.pending.get(user_id, ()))

    def remember_selector(self, request_id: int, owner_key: TopicKey) -> None:
        self._owner_key(owner_key)
        if not -(2**31) <= request_id < 2**31:
            raise ValueError("selector request ID outside Telegram range")
        self.selectors[request_id] = owner_key
        self._save()

    def claim_selector(self, request_id: int) -> TopicKey | None:
        key = self.selectors.pop(request_id, None)
        if key is not None:
            self._save()
        return key

    def attach(self, owner_key: TopicKey, user_id: int, guest_key: TopicKey) -> None:
        self._owner_key(owner_key)
        self._guest_id(user_id)
        if _read_key(list(guest_key)) != guest_key or guest_key[0] != user_id:
            raise ValueError("guest topic must belong to invited user")
        existing = self._members.get((owner_key, user_id))
        if existing == guest_key:
            return
        if owner_key not in self.pending.get(user_id, ()):
            raise ValueError("no pending invitation for this user and topic")
        if guest_key in self._guest_to_owner:
            raise ValueError("guest topic is already shared")
        if existing:
            self._guest_to_owner.pop(existing, None)
        self._members[owner_key, user_id] = guest_key
        self._guest_to_owner[guest_key] = owner_key
        self.pending[user_id].discard(owner_key)
        if not self.pending[user_id]:
            self.pending.pop(user_id)
        self._save()

    def resolve(self, key: TopicKey) -> TopicKey | None:
        return self._guest_to_owner.get(key)

    def members(self, owner_key: TopicKey) -> list[tuple[int, TopicKey]]:
        return sorted(
            ((user_id, guest) for (key, user_id), guest in self._members.items() if key == owner_key),
            key=lambda value: value[0],
        )

    def linked_for(self, user_id: int) -> list[tuple[TopicKey, TopicKey]]:
        return sorted(
            ((owner, guest) for (owner, member_id), guest in self._members.items()
             if member_id == user_id),
            key=lambda value: value[0],
        )

    def replace_topic(self, owner_key: TopicKey, user_id: int, guest_key: TopicKey) -> None:
        """Replace only an existing member's deleted private topic."""
        self._owner_key(owner_key)
        self._guest_id(user_id)
        if (owner_key, user_id) not in self._members:
            raise ValueError("member is not linked")
        if _read_key(list(guest_key)) != guest_key or guest_key[0] != user_id:
            raise ValueError("replacement must belong to member")
        if guest_key in self._guest_to_owner:
            raise ValueError("replacement topic is already shared")
        old_key = self._members[owner_key, user_id]
        self._guest_to_owner.pop(old_key, None)
        self._members[owner_key, user_id] = guest_key
        self._guest_to_owner[guest_key] = owner_key
        self._save()

    def revoke(self, owner_key: TopicKey, user_id: int) -> None:
        self._owner_key(owner_key)
        self._guest_id(user_id)
        guest = self._members.pop((owner_key, user_id), None)
        if guest:
            self._guest_to_owner.pop(guest, None)
        pending = self.pending.get(user_id)
        if pending:
            pending.discard(owner_key)
            if not pending:
                self.pending.pop(user_id)
        self.links = {
            digest: value for digest, value in self.links.items()
            if value[0] != owner_key
        }
        self._save()
