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
