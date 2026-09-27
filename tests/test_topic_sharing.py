import json

from topic_sharing import ShareRegistry


OWNER = (101, "forum", 11)
GUEST = (202, "forum", 22)


def test_link_expires_and_is_one_use(tmp_path):
    registry = ShareRegistry(tmp_path / "shares.json", owner_id=101)
    token = registry.create_link(OWNER, now=100)

    assert token not in (tmp_path / "shares.json").read_text()
    assert registry.claim_link(token, user_id=202, now=101) == OWNER
    assert registry.claim_link(token, user_id=303, now=102) is None
    assert registry.pending_for(202) == [OWNER]

    expired = registry.create_link(OWNER, now=100)
    assert registry.claim_link(expired, user_id=303, now=100 + 7 * 86400) is None


def test_claim_persists_pending_after_reload(tmp_path):
    path = tmp_path / "shares.json"
    registry = ShareRegistry(path, owner_id=101)
    token = registry.create_link(OWNER, now=100)
    registry.claim_link(token, user_id=202, now=101)

    restored = ShareRegistry(path, owner_id=101)
    assert restored.pending_for(202) == [OWNER]
    assert restored.claim_link(token, user_id=303, now=102) is None


def test_unknown_or_ambiguous_username_never_resolves_to_id(tmp_path):
    registry = ShareRegistry(tmp_path / "shares.json", owner_id=101)
    assert registry.known_user_id("@alex") is None
    registry.record_user(202, "Alex", "Alex One", private_started=True)
    assert registry.known_user_id("@aLeX") == 202
    registry.record_user(303, "alex", "Alex Two", private_started=True)
    assert registry.known_user_id("@alex") is None
    registry.record_user(202, "newname", "Alex One", private_started=True)
    assert registry.known_user_id("@newname") == 202
    assert registry.known_user_id("@alex") == 303


def test_duplicate_attach_is_idempotent(tmp_path):
    registry = ShareRegistry(tmp_path / "shares.json", owner_id=101)
    registry.invite_user(OWNER, 202)
    registry.attach(OWNER, 202, GUEST)
    registry.attach(OWNER, 202, GUEST)

    assert registry.members(OWNER) == [(202, GUEST)]
    assert registry.resolve(GUEST) == OWNER
    assert registry.pending_for(202) == []


def test_revoke_invalidates_unused_links(tmp_path):
    registry = ShareRegistry(tmp_path / "shares.json", owner_id=101)
    token = registry.create_link(OWNER, now=100)
    registry.invite_user(OWNER, 202)
    registry.attach(OWNER, 202, GUEST)

    registry.revoke(OWNER, 202)

    assert registry.members(OWNER) == []
    assert registry.resolve(GUEST) is None
    assert registry.claim_link(token, user_id=303, now=101) is None


def test_selector_survives_reload_once(tmp_path):
    path = tmp_path / "shares.json"
    registry = ShareRegistry(path, owner_id=101)
    registry.remember_selector(42, OWNER)

    restored = ShareRegistry(path, owner_id=101)
    assert restored.claim_selector(42) == OWNER
    assert restored.claim_selector(42) is None
    assert json.loads(path.read_text())["selectors"] == {}


def test_malformed_sections_do_not_crash_registry_load(tmp_path):
    path = tmp_path / "shares.json"
    path.write_text(json.dumps({
        "users": [{}], "links": [{}], "pending": [{}], "selectors": [{}],
        "members": [None, {"owner": list(OWNER), "user_id": 202, "guest": list(GUEST)}],
    }))

    registry = ShareRegistry(path, owner_id=101)

    assert registry.members(OWNER) == [(202, GUEST)]
