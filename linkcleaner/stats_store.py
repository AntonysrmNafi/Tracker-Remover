"""Usage stats and moderation state, backed by SQLite.

Covers three things:
  - per-user stats (links cleaned total/today/week/month) — used by the
    "👤 Profile" button
  - global stats + popular domains — used by the admin panel's "Statistics"
  - a simple blocklist — used by the admin panel's "User Control"

The database path is STATS_DB_PATH (default: "linkcleaner_stats.db" in the
working directory). On Railway/most PaaS the filesystem is ephemeral: data
resets on every redeploy unless you mount a persistent Volume and point
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
from dataclasses import dataclass, field

DB_PATH = os.environ.get("STATS_DB_PATH", "linkcleaner_stats.db")

DAY_SECONDS = 86_400
WEEK_SECONDS = 7 * DAY_SECONDS
MONTH_SECONDS = 30 * DAY_SECONDS

DEFAULT_TOP_DOMAINS_LIMIT = 10


@dataclass
class UserStats:
    total: int
    today: int
    week: int
    month: int
    first_seen: float | None  # unix timestamp, None if never recorded


@dataclass
class GlobalStats:
    total: int
    today: int
    week: int
    month: int


@dataclass
class DomainCount:
    domain: str
    count: int


@dataclass
class BlockedUser:
    user_id: int
    username: str | None
    first_name: str | None
    blocked_at: float


@dataclass
class UserInfo:
    user_id: int
    username: str | None
    first_name: str | None
    first_seen: float | None
    blocked: bool
    blocked_at: float | None
    stats: UserStats
    top_domains: list[DomainCount] = field(default_factory=list)

    @property
    def is_known(self) -> bool:
        """False if this user has never messaged the bot (no row at all)."""
        return self.first_seen is not None or self.stats.total > 0


# ---------------------------------------------------------------------------
# Schema. New columns are added via best-effort ALTER TABLE for anyone
# upgrading from an older DB; CREATE TABLE already includes them for a fresh
# one, so the ALTERs below just no-op (column already exists) in that case.
# ---------------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            first_seen_at REAL NOT NULL,
            blocked INTEGER NOT NULL DEFAULT 0,
            blocked_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS link_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            cleaned_at REAL NOT NULL,
            domain TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_link_events_user_time ON link_events(user_id, cleaned_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_link_events_domain ON link_events(domain)")
    for statement in (
        "ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN blocked_at REAL",
        "ALTER TABLE link_events ADD COLUMN domain TEXT",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass  # column already exists
    return conn


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
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


def _record_events_sync(user_id: int, domains: list[str], now: float) -> None:
    with closing(_connect()) as conn, conn:
        conn.executemany(
            "INSERT INTO link_events (user_id, cleaned_at, domain) VALUES (?, ?, ?)",
            [(user_id, now, domain) for domain in domains],
        )


def _set_blocked_sync(user_id: int, blocked: bool, now: float) -> None:
    with closing(_connect()) as conn, conn:
        cursor = conn.execute(
            "UPDATE users SET blocked = ?, blocked_at = ? WHERE user_id = ?",
            (1 if blocked else 0, now if blocked else None, user_id),
        )
        if cursor.rowcount == 0:
            # This user has never messaged the bot before; insert a minimal
            # row so the block still takes effect if/when they do.
            conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, first_seen_at, blocked, blocked_at)
                VALUES (?, NULL, NULL, ?, ?, ?)
                """,
                (user_id, now, 1 if blocked else 0, now if blocked else None),
            )


async def touch_user(user_id: int, username: str | None, first_name: str | None) -> None:
    """Records/updates basic profile info for a user. Safe to call on every
    message; first_seen_at is only ever set once (on first insert)."""
    await asyncio.to_thread(_touch_user_sync, user_id, username, first_name, time.time())


async def record_links_cleaned(user_id: int, domains: list[str]) -> None:
    """Records that a link was just cleaned for this user, once per domain
    in `domains` (one entry per link, e.g. ["youtube.com", "x.com"])."""
    if not domains:
        return
    await asyncio.to_thread(_record_events_sync, user_id, domains, time.time())


async def block_user(user_id: int) -> None:
    await asyncio.to_thread(_set_blocked_sync, user_id, True, time.time())


async def unblock_user(user_id: int) -> None:
    await asyncio.to_thread(_set_blocked_sync, user_id, False, time.time())


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _count_since(conn: sqlite3.Connection, user_id: int | None, cutoff: float | None) -> int:
    conditions = []
    params: list = []
    if user_id is not None:
        conditions.append("user_id = ?")
        params.append(user_id)
    if cutoff is not None:
        conditions.append("cleaned_at >= ?")
        params.append(cutoff)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return conn.execute(f"SELECT COUNT(*) FROM link_events{where}", params).fetchone()[0]


def _get_stats_sync(user_id: int) -> UserStats:
    now = time.time()
    with closing(_connect()) as conn:
        total = _count_since(conn, user_id, None)
        today = _count_since(conn, user_id, now - DAY_SECONDS)
        week = _count_since(conn, user_id, now - WEEK_SECONDS)
        month = _count_since(conn, user_id, now - MONTH_SECONDS)
        row = conn.execute(
            "SELECT first_seen_at FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        first_seen = row[0] if row else None
    return UserStats(total=total, today=today, week=week, month=month, first_seen=first_seen)


def _get_global_stats_sync() -> GlobalStats:
    now = time.time()
    with closing(_connect()) as conn:
        total = _count_since(conn, None, None)
        today = _count_since(conn, None, now - DAY_SECONDS)
        week = _count_since(conn, None, now - WEEK_SECONDS)
        month = _count_since(conn, None, now - MONTH_SECONDS)
    return GlobalStats(total=total, today=today, week=week, month=month)


def _get_top_domains_sync(user_id: int | None, limit: int) -> list[DomainCount]:
    with closing(_connect()) as conn:
        if user_id is None:
            rows = conn.execute(
                """
                SELECT domain, COUNT(*) AS c FROM link_events
                WHERE domain IS NOT NULL AND domain != ''
                GROUP BY domain ORDER BY c DESC, domain ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT domain, COUNT(*) AS c FROM link_events
                WHERE user_id = ? AND domain IS NOT NULL AND domain != ''
                GROUP BY domain ORDER BY c DESC, domain ASC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
    return [DomainCount(domain=row[0], count=row[1]) for row in rows]


def _is_blocked_sync(user_id: int) -> bool:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return bool(row[0]) if row else False


def _list_blocked_users_sync() -> list[BlockedUser]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            """
            SELECT user_id, username, first_name, blocked_at FROM users
            WHERE blocked = 1 ORDER BY blocked_at DESC
            """
        ).fetchall()
    return [BlockedUser(user_id=r[0], username=r[1], first_name=r[2], blocked_at=r[3]) for r in rows]


def _get_user_row_sync(user_id: int):
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT username, first_name, first_seen_at, blocked, blocked_at FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()


async def get_user_stats(user_id: int) -> UserStats:
    return await asyncio.to_thread(_get_stats_sync, user_id)


async def get_global_stats() -> GlobalStats:
    return await asyncio.to_thread(_get_global_stats_sync)


async def get_top_domains(limit: int = DEFAULT_TOP_DOMAINS_LIMIT) -> list[DomainCount]:
    return await asyncio.to_thread(_get_top_domains_sync, None, limit)


async def get_top_domains_for_user(user_id: int, limit: int = 5) -> list[DomainCount]:
    return await asyncio.to_thread(_get_top_domains_sync, user_id, limit)


async def is_blocked(user_id: int) -> bool:
    return await asyncio.to_thread(_is_blocked_sync, user_id)


async def list_blocked_users() -> list[BlockedUser]:
    return await asyncio.to_thread(_list_blocked_users_sync)


async def get_user_info(user_id: int) -> UserInfo:
    row = await asyncio.to_thread(_get_user_row_sync, user_id)
    stats = await get_user_stats(user_id)
    top_domains = await get_top_domains_for_user(user_id)

    if row is None:
        return UserInfo(
            user_id=user_id, username=None, first_name=None, first_seen=None,
            blocked=False, blocked_at=None, stats=stats, top_domains=top_domains,
        )

    username, first_name, first_seen_at, blocked, blocked_at = row
    return UserInfo(
        user_id=user_id, username=username, first_name=first_name, first_seen=first_seen_at,
        blocked=bool(blocked), blocked_at=blocked_at, stats=stats, top_domains=top_domains,
    )
