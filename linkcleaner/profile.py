"""Builds the text shown when a user taps the "Profile" button: their basic
info plus how many links they've had cleaned (total/today/week/month)."""

from datetime import datetime, timezone

from linkcleaner import stats_store
from linkcleaner.stats_store import UserStats


def format_profile_text(
    user_id: int,
    first_name: str | None,
    username: str | None,
    stats: UserStats,
) -> str:
    display_name = first_name or "there"
    username_line = f"@{username}" if username else "(none)"

    if stats.first_seen is not None:
        member_since = datetime.fromtimestamp(stats.first_seen, tz=timezone.utc).strftime("%Y-%m-%d")
    else:
        member_since = "just now"

    return (
        f"👤 {display_name}'s Profile\n\n"
        f"Username : {username_line}\n"
        f"User ID : {user_id}\n"
        f"Member since : {member_since}\n\n"
        f"📊 Links Cleaned\n"
        f"Total : {stats.total}\n"
        f"Today (last 24h) : {stats.today}\n"
        f"This week (last 7d) : {stats.week}\n"
        f"This month (last 30d) : {stats.month}"
    )


async def get_profile_text(user_id: int, first_name: str | None, username: str | None) -> str:
    stats = await stats_store.get_user_stats(user_id)
    return format_profile_text(user_id, first_name, username, stats)
