from datetime import datetime, timezone

import linkcleaner.profile as profile
from linkcleaner.stats_store import UserStats


def test_format_profile_text_includes_name_and_stats():
    stats = UserStats(total=42, today=3, week=12, month=30, first_seen=None)
    text = profile.format_profile_text(123456, "Alice", "alice_w", stats)

    assert "Alice's Profile" in text
    assert "@alice_w" in text
    assert "123456" in text
    assert "Total : 42" in text
    assert "Today (last 24h) : 3" in text
    assert "This week (last 7d) : 12" in text
    assert "This month (last 30d) : 30" in text


def test_format_profile_text_handles_no_username():
    stats = UserStats(total=0, today=0, week=0, month=0, first_seen=None)
    text = profile.format_profile_text(1, "Bob", None, stats)
    assert "(none)" in text


def test_format_profile_text_handles_missing_first_name():
    stats = UserStats(total=0, today=0, week=0, month=0, first_seen=None)
    text = profile.format_profile_text(1, None, None, stats)
    assert "there's Profile" in text


def test_format_profile_text_formats_member_since_date():
    dt = datetime(2026, 7, 20, tzinfo=timezone.utc)
    stats = UserStats(total=1, today=1, week=1, month=1, first_seen=dt.timestamp())
    text = profile.format_profile_text(1, "Alice", "alice", stats)
    assert "Member since : 2026-07-20" in text


def test_format_profile_text_shows_just_now_for_brand_new_user():
    stats = UserStats(total=0, today=0, week=0, month=0, first_seen=None)
    text = profile.format_profile_text(1, "Alice", "alice", stats)
    assert "Member since : just now" in text


async def test_get_profile_text_pulls_from_stats_store(monkeypatch):
    async def _fake_get_user_stats(user_id):
        return UserStats(total=7, today=1, week=2, month=7, first_seen=None)

    monkeypatch.setattr(profile.stats_store, "get_user_stats", _fake_get_user_stats)

    text = await profile.get_profile_text(999, "Carol", "carol")
    assert "Total : 7" in text
    assert "Carol's Profile" in text
