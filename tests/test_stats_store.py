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
    await stats_store.record_links_cleaned(222, 3)
    await stats_store.record_links_cleaned(222, 2)
    stats = await stats_store.get_user_stats(222)
    assert stats.total == 5


async def test_record_links_cleaned_zero_is_a_noop():
    await stats_store.record_links_cleaned(333, 0)
    stats = await stats_store.get_user_stats(333)
    assert stats.total == 0


async def test_stats_are_scoped_per_user():
    await stats_store.record_links_cleaned(1, 4)
    await stats_store.record_links_cleaned(2, 1)

    assert (await stats_store.get_user_stats(1)).total == 4
    assert (await stats_store.get_user_stats(2)).total == 1


async def test_today_week_month_all_include_a_just_recorded_event():
    await stats_store.record_links_cleaned(444, 1)
    stats = await stats_store.get_user_stats(444)
    assert stats.total == 1
    assert stats.today == 1
    assert stats.week == 1
    assert stats.month == 1


async def test_old_events_outside_window_are_excluded(monkeypatch):
    # Insert an event 10 days old directly, bypassing "now" timing.
    now = time.time()
    ten_days_ago = now - (10 * stats_store.DAY_SECONDS)

    def _record_at(user_id, count, at_time):
        stats_store._record_events_sync(user_id, count, at_time)

    _record_at(555, 1, ten_days_ago)

    stats = await stats_store.get_user_stats(555)
    assert stats.total == 1       # still counted overall
    assert stats.today == 0       # too old for the 24h window
    assert stats.week == 0        # too old for the 7-day window
    assert stats.month == 1       # within the 30-day window
