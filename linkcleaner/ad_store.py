"""Ad campaigns, backed by SQLite (same DB file as stats_store).

An ad is created in a few steps (content, optional button, expiry hours),
saved to the DB as soon as its content is received (status "draft"), then
broadcast to every known user via copy_message once the admin confirms.
Each per-recipient delivery is tracked in ad_deliveries so the bot can
delete the ad from each recipient's chat once it expires — see
linkcleaner.ad_service for the sending/expiring logic that talks to
Telegram.
"""

import asyncio
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass

DB_PATH = os.environ.get("STATS_DB_PATH", "linkcleaner_stats.db")

MIN_EXPIRE_HOURS = 1
MAX_EXPIRE_HOURS = 70


@dataclass
class Ad:
    id: int
    source_chat_id: int
    source_message_id: int
    button_text: str | None
    button_url: str | None
    expire_hours: int
    created_at: float
    status: str  # "draft", "sent", "expired", "cancelled"
    sent_at: float | None
    expires_at: float | None
    sent_count: int


@dataclass
class AdDelivery:
    id: int
    ad_id: int
    user_id: int
    sent_message_id: int
    deleted: bool


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_chat_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            button_text TEXT,
            button_url TEXT,
            expire_hours INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            sent_at REAL,
            expires_at REAL,
            sent_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ad_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ad_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            sent_message_id INTEGER NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ad_deliveries_ad ON ad_deliveries(ad_id)")
    return conn


def _row_to_ad(row) -> Ad:
    return Ad(
        id=row[0], source_chat_id=row[1], source_message_id=row[2],
        button_text=row[3], button_url=row[4], expire_hours=row[5],
        created_at=row[6], status=row[7], sent_at=row[8], expires_at=row[9],
        sent_count=row[10],
    )


def _create_ad_sync(source_chat_id: int, source_message_id: int, now: float) -> int:
    with closing(_connect()) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO ads (source_chat_id, source_message_id, expire_hours, created_at, status)
            VALUES (?, ?, 0, ?, 'draft')
            """,
            (source_chat_id, source_message_id, now),
        )
        return cursor.lastrowid


def _update_button_sync(ad_id: int, button_text: str | None, button_url: str | None) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE ads SET button_text = ?, button_url = ? WHERE id = ?", (button_text, button_url, ad_id))


def _update_expire_hours_sync(ad_id: int, hours: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE ads SET expire_hours = ? WHERE id = ?", (hours, ad_id))


def _mark_sent_sync(ad_id: int, sent_count: int, now: float, expires_at: float) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE ads SET status = 'sent', sent_at = ?, expires_at = ?, sent_count = ? WHERE id = ?",
            (now, expires_at, sent_count, ad_id),
        )


def _mark_cancelled_sync(ad_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE ads SET status = 'cancelled' WHERE id = ?", (ad_id,))


def _mark_expired_sync(ad_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE ads SET status = 'expired' WHERE id = ?", (ad_id,))


def _get_ad_sync(ad_id: int):
    with closing(_connect()) as conn:
        return conn.execute("SELECT * FROM ads WHERE id = ?", (ad_id,)).fetchone()


def _record_delivery_sync(ad_id: int, user_id: int, sent_message_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO ad_deliveries (ad_id, user_id, sent_message_id, deleted) VALUES (?, ?, ?, 0)",
            (ad_id, user_id, sent_message_id),
        )


def _get_active_deliveries_sync(ad_id: int) -> list:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT id, ad_id, user_id, sent_message_id, deleted FROM ad_deliveries WHERE ad_id = ? AND deleted = 0",
            (ad_id,),
        ).fetchall()
    return [AdDelivery(id=r[0], ad_id=r[1], user_id=r[2], sent_message_id=r[3], deleted=bool(r[4])) for r in rows]


def _mark_delivery_deleted_sync(delivery_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE ad_deliveries SET deleted = 1 WHERE id = ?", (delivery_id,))


def _get_pending_expiry_ads_sync() -> list:
    """Ads that were sent, have an expiry set, and aren't expired/cancelled yet."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM ads WHERE status = 'sent' AND expires_at IS NOT NULL").fetchall()
    return [_row_to_ad(row) for row in rows]


async def create_ad(source_chat_id: int, source_message_id: int) -> int:
    return await asyncio.to_thread(_create_ad_sync, source_chat_id, source_message_id, time.time())


async def update_ad_button(ad_id: int, button_text: str | None, button_url: str | None) -> None:
    await asyncio.to_thread(_update_button_sync, ad_id, button_text, button_url)


async def update_ad_expire_hours(ad_id: int, hours: int) -> None:
    await asyncio.to_thread(_update_expire_hours_sync, ad_id, hours)


async def mark_ad_sent(ad_id: int, sent_count: int, expires_at: float) -> None:
    await asyncio.to_thread(_mark_sent_sync, ad_id, sent_count, time.time(), expires_at)


async def mark_ad_cancelled(ad_id: int) -> None:
    await asyncio.to_thread(_mark_cancelled_sync, ad_id)


async def mark_ad_expired(ad_id: int) -> None:
    await asyncio.to_thread(_mark_expired_sync, ad_id)


async def get_ad(ad_id: int) -> Ad | None:
    row = await asyncio.to_thread(_get_ad_sync, ad_id)
    return _row_to_ad(row) if row else None


async def record_delivery(ad_id: int, user_id: int, sent_message_id: int) -> None:
    await asyncio.to_thread(_record_delivery_sync, ad_id, user_id, sent_message_id)


async def get_active_deliveries(ad_id: int) -> list[AdDelivery]:
    return await asyncio.to_thread(_get_active_deliveries_sync, ad_id)


async def mark_delivery_deleted(delivery_id: int) -> None:
    await asyncio.to_thread(_mark_delivery_deleted_sync, delivery_id)


async def get_pending_expiry_ads() -> list[Ad]:
    return await asyncio.to_thread(_get_pending_expiry_ads_sync)
