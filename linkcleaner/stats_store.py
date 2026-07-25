"""Per-user usage stats (links cleaned total/today/week/month), backed by
SQLite.

The database path is STATS_DB_PATH (default: "linkcleaner_stats.db" in the
working directory). On Railway/most PaaS the filesystem is ephemeral: stats
reset on every redeploy unless you mount a persistent Volume and point
STATS_DB_PATH at a file inside it (e.g. "/data/linkcleaner_stats.db").

"Today" / "this week" / "this month" are rolling windows (last 24h / 7d /
30d), not calendar boundaries — that avoids picking a timezone to define
"midnight" or "start of week" for every user.

All sqlite3 calls are synchronous; they're run via asyncio.to_thread so they
never block the event loop. SQLite handles this level of traffic (a small
Telegram bot) comfortably.
"""

import asyncio
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass

DB_PATH = os.environ.get("STATS_DB_PATH", "linkcleaner_stats.db")

DAY_SECONDS = 86_400
WEEK_SECONDS = 7 * DAY_SECONDS
MONTH_SECONDS = 30 * DAY_SECONDS


@dataclass
class UserStats:
    total: int
    today: int
    week: int
    month: int
    first_seen: float | None  # unix timestamp, None if never recorded


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            first_seen_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS link_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            cleaned_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_link_events_user_time ON link_events(user_id, cleaned_at)"
    )
    return conn


def _touch_user_sync(user_id: int, username: str | None, first_name: str | None, now: float) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, first_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (user_id, username, first_name, now),
        )


def _record_events_sync(user_id: int, count: int, now: float) -> None:
    with closing(_connect()) as conn, conn:
        conn.executemany(
            "INSERT INTO link_events (user_id, cleaned_at) VALUES (?, ?)",
            [(user_id, now)] * count,
        )


def _get_stats_sync(user_id: int) -> UserStats:
    now = time.time()
    with closing(_connect()) as conn:
        def count_since(cutoff: float | None) -> int:
            if cutoff is None:
                query, params = "SELECT COUNT(*) FROM link_events WHERE user_id = ?", (user_id,)
            else:
                query = "SELECT COUNT(*) FROM link_events WHERE user_id = ? AND cleaned_at >= ?"
                params = (user_id, cutoff)
            return conn.execute(query, params).fetchone()[0]

        total = count_since(None)
        today = count_since(now - DAY_SECONDS)
        week = count_since(now - WEEK_SECONDS)
        month = count_since(now - MONTH_SECONDS)

        row = conn.execute(
            "SELECT first_seen_at FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        first_seen = row[0] if row else None

    return UserStats(total=total, today=today, week=week, month=month, first_seen=first_seen)


async def touch_user(user_id: int, username: str | None, first_name: str | None) -> None:
    """Records/updates basic profile info for a user. Safe to call on every
    message; first_seen_at is only ever set once (on first insert)."""
    await asyncio.to_thread(_touch_user_sync, user_id, username, first_name, time.time())


async def record_links_cleaned(user_id: int, count: int) -> None:
    """Records that `count` links were just cleaned for this user."""
    if count <= 0:
        return
    await asyncio.to_thread(_record_events_sync, user_id, count, time.time())


async def get_user_stats(user_id: int) -> UserStats:
    return await asyncio.to_thread(_get_stats_sync, user_id)
