# Shared Telegram Topics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Share one Codex topic across the owner's and invited users' private bot chats, mirroring new messages while reserving controls and approvals for the owner.

**Architecture:** Keep one canonical `Session` keyed by the owner's topic. A persistent sharing registry maps each guest topic to that key and tracks invitations. Admission and origin attribution happen before session lookup; a delivery layer fans out shared transcript messages while owner-only UI stays local.

**Tech Stack:** Python 3, aiogram, Telegram Bot API, pytest; existing `bot.py` and JSON state pattern.

**Spec:** `docs/superpowers/specs/2026-09-27-shared-telegram-topics-design.md`

## Global Constraints

- Only private bot-chat topics are shareable; no group/forum membership or per-topic visibility claims.
- Only new messages after attachment are mirrored; no historical backfill or guaranteed delete synchronization.
- Guests may send supported text/media but may not run control commands, callbacks, or approvals.
- Guest-origin turns use `approvalPolicy=on-request` and `workspaceWrite` even while owner-enabled full access is active.
- A failed guest delivery never blocks the canonical Codex turn or delivery to another participant.
- Preserve all pre-existing uncommitted edits to `bot.py`, `README.md`, and unrelated paths.

## Review Focus

1. A username changed or duplicated after invitation must never grant access by name alone — Task 1 test `test_unknown_or_ambiguous_username_never_resolves_to_id`.
2. A copied owner approval button or forged callback from a guest must not decide a request — Task 2 test `test_guest_callback_cannot_reach_owner_handler`.
3. A token consumed just before Telegram topic creation fails must remain recoverable for the same user — Task 3 test `test_failed_topic_creation_keeps_id_pending`.
4. A guest message queued behind an owner message must retain the guest's identity and sandbox policy — Task 4 test `test_queued_guest_turn_retains_origin_and_policy`.
5. A blocked guest must not stop delivery to the owner or healthy guests — Task 6 test `test_blocked_guest_isolated_and_owner_notified_once`.

---

### Task 1: Durable sharing registry

**Files:** Create `topic_sharing.py`; test `tests/test_topic_sharing.py`.

**Interfaces:** Define `TopicKey = tuple[int, str, int]` and `ShareRegistry(path: Path, owner_id: int)`. Expose `record_user(user_id: int, username: str | None, display_name: str, private_started: bool)`, `known_user_id(username: str) -> int | None`, `create_link(owner_key: TopicKey, now: float) -> str`, `claim_link(token: str, user_id: int, now: float) -> TopicKey | None`, `invite_user(owner_key: TopicKey, user_id: int)`, `pending_for(user_id: int) -> list[TopicKey]`, `remember_selector(request_id: int, owner_key: TopicKey)`, `claim_selector(request_id: int) -> TopicKey | None`, `attach(owner_key: TopicKey, user_id: int, guest_key: TopicKey)`, `resolve(key: TopicKey) -> TopicKey | None`, `members(owner_key: TopicKey) -> list[tuple[int, TopicKey]]`, and `revoke(owner_key: TopicKey, user_id: int)`. `claim_link` consumes the seven-day token and saves an ID-bound pending invitation before topic creation. Persist with atomic replace; store SHA-256 token digest, never raw token. Reject corrupt, duplicate, non-private, and self-links; preserve valid entries on reload. Selector requests persist across restart and are one-use.

- [ ] Write failing tests `test_link_expires_and_is_one_use`, `test_claim_persists_pending_after_reload`, `test_unknown_or_ambiguous_username_never_resolves_to_id`, `test_duplicate_attach_is_idempotent`, `test_revoke_invalidates_unused_links`, and `test_selector_survives_reload_once`. Assert that a second claim returns `None`, the raw token is absent from JSON, and `resolve(guest_key) == owner_key` only while attached.
- [ ] Run `pytest -q tests/test_topic_sharing.py`; expect failures for missing registry.
- [ ] Implement the interfaces in `topic_sharing.py`, with focused validation and atomic JSON persistence.
- [ ] Run `pytest -q tests/test_topic_sharing.py`; expect all pass.
- [ ] Commit only `topic_sharing.py` and `tests/test_topic_sharing.py`.

### Task 2: Admission, canonical sessions, and owner-only controls

**Files:** Modify `bot.py` around `_register_handlers`, `_session`, `on_start`, and turn policy; test `tests/test_shared_authorization.py`.

**Interfaces:** `self.shares: ShareRegistry`; `_topic_key(message: Message) -> TopicKey`; `_authorized_input(message: Message) -> bool`; `_owner_only(event: Message | CallbackQuery) -> bool`; `_session(message: Message) -> Session` resolves aliases through `shares.resolve`; `_thread_access_params(session: Session, *, guest_turn: bool = False) -> dict[str, Any]`. Extend `QueuedInput` with `origin_user_id`, `origin_name`, and `guest_turn`; pass the latter through `_start_user_turn` to the turn policy. Existing owner behavior remains unchanged.

- [ ] Write failing tests `test_unknown_guest_creates_no_session`, `test_linked_guest_uses_owner_session`, `test_guest_callback_cannot_reach_owner_handler`, and `test_guest_turn_downgrades_full_access`. Assert session object identity, denied control handlers, and exact `approvalPolicy == "on-request"` plus `sandboxPolicy.type == "workspaceWrite"`; repeat for owner access unchanged.
- [ ] Run `pytest -q tests/test_shared_authorization.py`; expect failures.
- [ ] Remove the global owner router filter only after replacing it with explicit owner-only filters for every control message/callback, public `/start`, and shared-input admission. Reject guest-created unrelated topics and service updates.
- [ ] Run `pytest -q tests/test_shared_authorization.py tests/test_provider_switching.py`; expect all pass.
- [ ] Commit only Task 2 code and tests, preserving unrelated existing `bot.py` changes.

### Task 3: Share, join, and revoke workflows

**Files:** Modify `bot.py`; test `tests/test_shared_invites.py`.

**Interfaces:** Add owner-only `on_share(message: Message)`, `on_users_shared(message: Message)`, and `on_unshare(message: Message)`; make public `on_start` recognize deep-link tokens and ID-bound pending invitations. `_activate_pending(owner_key: TopicKey, user_id: int) -> TopicKey | None` calls `create_forum_topic` in the guest's private chat, attaches it to the registry, and sends both sides a notice. Persistent `ShareRegistry.remember_selector`/`claim_selector` ties Telegram's user-selector response to the originating topic. `/share @username` resolves only known stable IDs; an unknown name returns the generic link and an explicit explanation.

- [ ] Write failing async tests `test_link_join_creates_guest_topic`, `test_known_id_invite_notifies_now`, `test_id_invite_activates_on_start`, `test_unknown_username_returns_link`, `test_failed_topic_creation_keeps_id_pending`, `test_repeated_start_creates_one_topic`, `test_selector_targets_original_topic`, and `test_unshare_revokes_immediately`. Assert `create_forum_topic` call count and chat ID, persisted pending state, exact canonical key, and zero guest deliveries after revocation.
- [ ] Run `pytest -q tests/test_shared_invites.py`; expect failures.
- [ ] Implement handlers and register `/share`, `/unshare`, and `users_shared`; use a seven-day link lifetime and no unsolicited messages to people who have never started the bot.
- [ ] Run `pytest -q tests/test_shared_invites.py tests/test_shared_authorization.py`; expect all pass.
- [ ] Commit only Task 3 code and tests.

### Task 4: Participant messages and agent attribution

**Files:** Create `shared_delivery.py`; modify `bot.py` input handlers and `QueuedInput`; test `tests/test_shared_inputs.py`.

**Interfaces:** `SharedDelivery(bot, shares)` exposes `mirror_human(message: Message, source_key: TopicKey, author_label: str) -> None`; text uses a bot-authored name/ID header, supported media uses `copy_message` plus a header in the target topic. `_label_input(input_items: list[dict[str, Any]], user_id: int, name: str) -> list[dict[str, Any]]` inserts a bot-controlled author envelope before submitting to Codex, including file/image/audio prompts. Ignore bot-authored echoed messages; no forwarding loop.

- [ ] Write failing tests `test_text_mirrors_both_directions`, `test_two_guests_receive_other_guest_message`, `test_media_copy_targets_correct_thread`, `test_author_id_reaches_codex_input`, `test_queued_guest_turn_retains_origin_and_policy`, and `test_unsupported_media_does_not_start_turn`. Assert target `message_thread_id`, author ID in the queued item, and zero turn starts for unsupported media.
- [ ] Run `pytest -q tests/test_shared_inputs.py`; expect failures.
- [ ] Implement mirroring once at admission and attribution once before queueing, keeping existing media size/filter behavior.
- [ ] Run `pytest -q tests/test_shared_inputs.py tests/test_turn_steering.py`; expect all pass.
- [ ] Commit only Task 4 code and tests.

### Task 5: Agent output fan-out without leaking controls

**Files:** Extend `shared_delivery.py`; modify `bot.py` agent-event send/edit call sites; test `tests/test_shared_outputs.py`.

**Interfaces:** `broadcast_html(owner_key: TopicKey, text: str, *, silent: bool) -> Message` and `broadcast_markdown(owner_key: TopicKey, text: str, *, silent: bool) -> list[Message]` return owner messages for existing code while storing guest message IDs for later edits. `broadcast_edit(owner_key: TopicKey, owner_message_id: int, text: str) -> None` edits each available copy. Approval cards remain owner-only; `notify_approval_waiting(owner_key: TopicKey)` sends guests a read-only status. Menus, OAuth actions, and command responses stay local.

- [ ] Write failing tests `test_agent_final_reaches_all_members`, `test_status_edit_uses_each_chat_message_id`, `test_approval_buttons_owner_only`, `test_guest_sees_read_only_approval_status`, and `test_control_menus_stay_owner_only`. Assert each member receives the same answer, guest `reply_markup is None`, and only the owner's approval callback can respond.
- [ ] Run `pytest -q tests/test_shared_outputs.py`; expect failures.
- [ ] Replace only transcript/status call sites with broadcast methods; keep control/UI call sites on existing single-recipient methods.
- [ ] Run `pytest -q tests/test_shared_outputs.py tests/test_turn_presentation.py tests/test_subagents.py`; expect all pass.
- [ ] Commit only Task 5 code and tests.

### Task 6: Recovery, documentation, and end-to-end regression

**Files:** Modify `bot.py`, `shared_delivery.py`, and `README.md`; test `tests/test_shared_recovery.py`.

**Interfaces:** `_activate_pending` handles a deleted guest topic by creating one replacement after `/start`; delivery detects Telegram forbidden/not-found separately from `retry_after`, performs bounded retry, and records one owner notice per broken member until recovery. A received `edited_message` updates the mapped human-message copy when available. No deletion propagation or historical backfill.

- [ ] Write failing tests `test_blocked_guest_isolated_and_owner_notified_once`, `test_retry_after_is_bounded`, `test_reload_restores_pending_and_members`, `test_deleted_guest_topic_recreated_on_start`, `test_edited_text_updates_copy`, and `test_unshared_owner_topic_unchanged`. Assert healthy recipients still receive output and no more than one owner warning before recovery.
- [ ] Run `pytest -q tests/test_shared_recovery.py`; expect failures.
- [ ] Implement recovery and update `README.md` with `/share`, selector, `/unshare`, Telegram's no-first-message rule, visible guest scope, and history/delete limitations.
- [ ] Run `pytest -q` and `python -m py_compile bot.py topic_sharing.py shared_delivery.py`; expect success. Check `git diff --check` and review the exact diff against the spec.
- [ ] Commit only feature code/tests/docs; report any live Telegram behavior that could not be tested without a consenting second account.
