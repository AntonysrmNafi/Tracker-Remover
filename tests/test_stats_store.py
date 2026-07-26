import time

import pytest

import linkcleaner.stats_store as stats_store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets its own throwaway SQLite file so tests can't see
    each other's data and never touch the real stats DB."""
    db_path = tmp_path / "test_stats.db"
    monkeypatch.setattr(stats_store, "DB_PATH", str(db_path))
    yield db_path


# ---------------------------------------------------------------------------
# Per-user stats
# ---------------------------------------------------------------------------
async def test_new_user_has_zeroed_stats():
    stats = await stats_store.get_user_stats(12345)
    assert stats.total == 0
    assert stats.today == 0
    assert stats.week == 0
    assert stats.month == 0
    assert stats.first_seen is None


async def test_touch_user_sets_first_seen():
    before = time.time()
    await stats_store.touch_user(111, "alice", "Alice")
    stats = await stats_store.get_user_stats(111)
    assert stats.first_seen is not None
    assert stats.first_seen >= before


async def test_touch_user_does_not_reset_first_seen_on_repeat_calls():
    await stats_store.touch_user(111, "alice", "Alice")
    first_seen_1 = (await stats_store.get_user_stats(111)).first_seen

    await stats_store.touch_user(111, "alice_new_name", "Alice B.")
    first_seen_2 = (await stats_store.get_user_stats(111)).first_seen

    assert first_seen_1 == first_seen_2


async def test_record_links_cleaned_increments_total():
    await stats_store.record_links_cleaned(222, ["youtube.com", "x.com", "example.com"])
    await stats_store.record_links_cleaned(222, ["reddit.com", "example.com"])
    stats = await stats_store.get_user_stats(222)
    assert stats.total == 5


async def test_record_links_cleaned_empty_list_is_a_noop():
    await stats_store.record_links_cleaned(333, [])
    stats = await stats_store.get_user_stats(333)
    assert stats.total == 0


async def test_stats_are_scoped_per_user():
    await stats_store.record_links_cleaned(1, ["a.com"] * 4)
    await stats_store.record_links_cleaned(2, ["b.com"])

    assert (await stats_store.get_user_stats(1)).total == 4
    assert (await stats_store.get_user_stats(2)).total == 1


async def test_today_week_month_all_include_a_just_recorded_event():
    await stats_store.record_links_cleaned(444, ["example.com"])
    stats = await stats_store.get_user_stats(444)
    assert stats.total == 1
    assert stats.today == 1
    assert stats.week == 1
    assert stats.month == 1


async def test_old_events_outside_window_are_excluded():
    # Insert an event 10 days old directly, bypassing "now" timing.
    now = time.time()
    ten_days_ago = now - (10 * stats_store.DAY_SECONDS)
    stats_store._record_events_sync(555, ["example.com"], ten_days_ago)

    stats = await stats_store.get_user_stats(555)
    assert stats.total == 1       # still counted overall
    assert stats.today == 0       # too old for the 24h window
    assert stats.week == 0        # too old for the 7-day window
    assert stats.month == 1       # within the 30-day window


# ---------------------------------------------------------------------------
# Global stats + popular domains
# ---------------------------------------------------------------------------
async def test_global_stats_sum_across_users():
    await stats_store.record_links_cleaned(1, ["a.com", "b.com"])
    await stats_store.record_links_cleaned(2, ["a.com"])

    stats = await stats_store.get_global_stats()
    assert stats.total == 3
    assert stats.today == 3


async def test_top_domains_ranked_by_count():
    await stats_store.record_links_cleaned(1, ["youtube.com"] * 5)
    await stats_store.record_links_cleaned(2, ["youtube.com"] * 3)
    await stats_store.record_links_cleaned(1, ["x.com"] * 2)
    await stats_store.record_links_cleaned(3, ["reddit.com"])

    top = await stats_store.get_top_domains(limit=10)
    assert [dc.domain for dc in top] == ["youtube.com", "x.com", "reddit.com"]
    assert [dc.count for dc in top] == [8, 2, 1]


async def test_top_domains_respects_limit():
    for i in range(15):
        await stats_store.record_links_cleaned(1, [f"site{i}.com"])

    top = await stats_store.get_top_domains(limit=10)
    assert len(top) == 10


async def test_top_domains_for_user_scoped_correctly():
    await stats_store.record_links_cleaned(1, ["youtube.com"] * 3)
    await stats_store.record_links_cleaned(2, ["reddit.com"] * 5)

    top_for_1 = await stats_store.get_top_domains_for_user(1)
    assert [dc.domain for dc in top_for_1] == ["youtube.com"]


# ---------------------------------------------------------------------------
# Blocklist
# ---------------------------------------------------------------------------
async def test_new_user_is_not_blocked():
    assert await stats_store.is_blocked(999) is False


async def test_block_and_unblock_user():
    await stats_store.block_user(999)
    assert await stats_store.is_blocked(999) is True

    await stats_store.unblock_user(999)
    assert await stats_store.is_blocked(999) is False


async def test_block_user_who_never_messaged_the_bot():
    # Blocking someone by ID alone (they've never touched the bot before)
    # must still work and take effect.
    await stats_store.block_user(123456789)
    assert await stats_store.is_blocked(123456789) is True


async def test_block_preserves_existing_profile_info():
    await stats_store.touch_user(777, "dave", "Dave")
    await stats_store.block_user(777)

    info = await stats_store.get_user_info(777)
    assert info.username == "dave"
    assert info.blocked is True


async def test_list_blocked_users():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.touch_user(2, "bob", "Bob")
    await stats_store.block_user(1)

    blocked = await stats_store.list_blocked_users()
    assert len(blocked) == 1
    assert blocked[0].user_id == 1
    assert blocked[0].username == "alice"


async def test_list_blocked_users_empty_by_default():
    assert await stats_store.list_blocked_users() == []


# ---------------------------------------------------------------------------
# User info
# ---------------------------------------------------------------------------
async def test_user_info_for_unknown_user():
    info = await stats_store.get_user_info(424242)
    assert info.is_known is False
    assert info.username is None
    assert info.blocked is False


async def test_user_info_combines_profile_stats_and_domains():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.record_links_cleaned(1, ["youtube.com", "youtube.com", "x.com"])

    info = await stats_store.get_user_info(1)
    assert info.is_known is True
    assert info.username == "alice"
    assert info.stats.total == 3
    assert info.top_domains[0].domain == "youtube.com"
    assert info.top_domains[0].count == 2
