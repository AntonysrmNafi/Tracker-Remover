import pytest

import linkcleaner.admin_panel as admin_panel
import linkcleaner.stats_store as stats_store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_stats.db"
    monkeypatch.setattr(stats_store, "DB_PATH", str(db_path))
    yield db_path


# ---------------------------------------------------------------------------
# parse_user_id
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["123456789", "  123456789  ", "0"])
def test_parse_user_id_accepts_valid_ids(text):
    assert admin_panel.parse_user_id(text) == int(text.strip())


@pytest.mark.parametrize("text", ["abc", "", "12.5", "-123", "123abc", "@username"])
def test_parse_user_id_rejects_invalid_input(text):
    assert admin_panel.parse_user_id(text) is None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
async def test_build_statistics_text_shows_totals_and_domains():
    await stats_store.record_links_cleaned(1, ["youtube.com"] * 3)
    await stats_store.record_links_cleaned(2, ["x.com"])

    text = await admin_panel.build_statistics_text()
    assert "Total : 4" in text
    assert "Popular Domain" in text
    assert "1. youtube.com — 3" in text
    assert "2. x.com — 1" in text


async def test_build_statistics_text_with_no_data_omits_domain_section():
    text = await admin_panel.build_statistics_text()
    assert "Total : 0" in text
    assert "Popular Domain" not in text


# ---------------------------------------------------------------------------
# User info
# ---------------------------------------------------------------------------
async def test_build_user_info_text_for_unknown_user():
    text = await admin_panel.build_user_info_text(999999)
    assert "No record found" in text


async def test_build_user_info_text_for_known_user():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.record_links_cleaned(1, ["youtube.com", "youtube.com", "reddit.com"])

    text = await admin_panel.build_user_info_text(1)
    assert "User Info — 1" in text
    assert "Name : Alice" in text
    assert "@alice" in text
    assert "Status : ✅ Active" in text
    assert "Total : 3" in text
    assert "Top Domains" in text
    assert "1. youtube.com — 2" in text


async def test_build_user_info_text_shows_blocked_status():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.block_user(1)

    text = await admin_panel.build_user_info_text(1)
    assert "Status : 🚫 Blocked" in text


# ---------------------------------------------------------------------------
# Block / unblock
# ---------------------------------------------------------------------------
async def test_block_user_text_blocks_and_confirms():
    text = await admin_panel.block_user_text(555)
    assert "555" in text
    assert "blocked" in text.lower()
    assert await stats_store.is_blocked(555) is True


async def test_unblock_user_text_unblocks_and_confirms():
    await stats_store.block_user(555)
    text = await admin_panel.unblock_user_text(555)
    assert "555" in text
    assert "unblocked" in text.lower()
    assert await stats_store.is_blocked(555) is False


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
async def test_blocklist_csv_has_header_only_when_empty():
    csv_bytes = await admin_panel.build_blocklist_csv_bytes()
    text = csv_bytes.decode("utf-8")
    lines = text.strip().splitlines()
    assert len(lines) == 1
    assert lines[0] == "user_id,username,first_name,blocked_at_utc"


async def test_blocklist_csv_includes_blocked_users():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.block_user(1)
    await stats_store.touch_user(2, "bob", "Bob")  # not blocked

    csv_bytes = await admin_panel.build_blocklist_csv_bytes()
    text = csv_bytes.decode("utf-8")
    assert "1,alice,Alice" in text
    assert "bob" not in text


# ---------------------------------------------------------------------------
# resolve_broadcast_target
# ---------------------------------------------------------------------------
async def test_resolve_broadcast_target_by_numeric_id_for_known_user():
    await stats_store.touch_user(42, "alice", "Alice")
    assert await admin_panel.resolve_broadcast_target("42") == 42


async def test_resolve_broadcast_target_by_numeric_id_for_unknown_user_fails():
    assert await admin_panel.resolve_broadcast_target("999999") is None


async def test_resolve_broadcast_target_by_username():
    await stats_store.touch_user(42, "alice", "Alice")
    assert await admin_panel.resolve_broadcast_target("@alice") == 42


async def test_resolve_broadcast_target_by_username_case_insensitive():
    await stats_store.touch_user(42, "Alice", "Alice")
    assert await admin_panel.resolve_broadcast_target("@ALICE") == 42


async def test_resolve_broadcast_target_unknown_username_fails():
    assert await admin_panel.resolve_broadcast_target("@nobody") is None


async def test_resolve_broadcast_target_garbage_input_fails():
    assert await admin_panel.resolve_broadcast_target("not valid") is None


# ---------------------------------------------------------------------------
# parse_expire_hours
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["1", "70", "24", " 12 "])
def test_parse_expire_hours_accepts_valid_range(text):
    assert admin_panel.parse_expire_hours(text) == int(text.strip())


@pytest.mark.parametrize("text", ["0", "71", "100", "-5", "abc", "", "12.5"])
def test_parse_expire_hours_rejects_out_of_range_or_invalid(text):
    assert admin_panel.parse_expire_hours(text) is None


# ---------------------------------------------------------------------------
# validate_button_url
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["https://example.com", "http://example.com/page?x=1"])
def test_validate_button_url_accepts_valid_http_urls(url):
    assert admin_panel.validate_button_url(url) == url


@pytest.mark.parametrize("url", ["ftp://example.com", "example.com", "not a url", "javascript:alert(1)", ""])
def test_validate_button_url_rejects_invalid_input(url):
    assert admin_panel.validate_button_url(url) is None


# ---------------------------------------------------------------------------
# build_ad_preview_text
# ---------------------------------------------------------------------------
def test_build_ad_preview_text_with_button():
    text = admin_panel.build_ad_preview_text("Visit", "https://example.com", 24)
    assert "Auto-deletes after : 24h" in text
    assert "Button : Visit → https://example.com" in text


def test_build_ad_preview_text_without_button():
    text = admin_panel.build_ad_preview_text(None, None, 5)
    assert "Button : (none)" in text


# ---------------------------------------------------------------------------
# AD preview with pin
# ---------------------------------------------------------------------------
def test_build_ad_preview_text_shows_pinned_yes():
    text = admin_panel.build_ad_preview_text(None, None, 5, pinned=True)
    assert "Pinned : Yes" in text


def test_build_ad_preview_text_shows_pinned_no_by_default():
    text = admin_panel.build_ad_preview_text(None, None, 5)
    assert "Pinned : No" in text


# ---------------------------------------------------------------------------
# Maintenance message validation
# ---------------------------------------------------------------------------
def test_validate_maintenance_message_accepts_normal_text():
    assert admin_panel.validate_maintenance_message("Back in an hour!") == "Back in an hour!"


def test_validate_maintenance_message_rejects_empty():
    assert admin_panel.validate_maintenance_message("   ") is None


def test_validate_maintenance_message_rejects_over_limit():
    too_long = "x" * 2001
    assert admin_panel.validate_maintenance_message(too_long) is None


def test_validate_maintenance_message_accepts_at_limit():
    exactly_limit = "x" * 2000
    assert admin_panel.validate_maintenance_message(exactly_limit) == exactly_limit


def test_build_maintenance_status_text_on():
    import linkcleaner.settings_store as settings_store
    state = settings_store.MaintenanceState(enabled=True, message="Down for repairs")
    text = admin_panel.build_maintenance_status_text(state)
    assert "🟢 ON" in text
    assert "Down for repairs" in text


def test_build_maintenance_status_text_off():
    import linkcleaner.settings_store as settings_store
    state = settings_store.MaintenanceState(enabled=False, message="msg")
    text = admin_panel.build_maintenance_status_text(state)
    assert "⚪ OFF" in text


# ---------------------------------------------------------------------------
# Privacy-mode-aware User Info
# ---------------------------------------------------------------------------
async def test_user_info_hides_stats_when_privacy_mode_enabled():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.record_links_cleaned(1, ["youtube.com"])
    await stats_store.set_privacy_mode(1, True)

    text = await admin_panel.build_user_info_text(1)
    assert "Privacy Mode enabled" in text
    assert "Total :" not in text
    assert "youtube.com" not in text


async def test_user_info_shows_stats_when_privacy_mode_disabled():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.record_links_cleaned(1, ["youtube.com"])

    text = await admin_panel.build_user_info_text(1)
    assert "Total : 1" in text
    assert "youtube.com" in text
