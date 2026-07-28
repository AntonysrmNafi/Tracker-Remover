"""Broadcast history, backed by SQLite (same DB file as stats_store).

Every broadcast (public or to a specific user) is saved BEFORE it's sent, so
there's a durable record of what was broadcast and when. The bot always
sends from that saved copy via Telegram's copy_message, which works for any
message type — text, photo, video, etc. — without this module (or the admin
flow) needing to know what kind of content it is; we just remember which
chat + message ID the original content lives in.
"""

import asyncio
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass

DB_PATH = os.environ.get("STATS_DB_PATH", "linkcleaner_stats.db")


@dataclass
class Broadcast:
    id: int
    broadcast_type: str  # "public" or "specific"
    target_user_id: int | None
    source_chat_id: int
    source_message_id: int
    created_at: float
    status: str  # "pending", "sent", "failed"
    sent_count: int
    failed_count: int
    completed_at: float | None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broadcast_type TEXT NOT NULL,
            target_user_id INTEGER,
            source_chat_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            sent_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            completed_at REAL
        )
        """
    )
    return conn


def _row_to_broadcast(row) -> Broadcast:
    return Broadcast(
        id=row[0], broadcast_type=row[1], target_user_id=row[2],
        source_chat_id=row[3], source_message_id=row[4], created_at=row[5],
        status=row[6], sent_count=row[7], failed_count=row[8], completed_at=row[9],
    )


def _create_broadcast_sync(
    broadcast_type: str, target_user_id: int | None, source_chat_id: int, source_message_id: int, now: float
) -> int:
    with closing(_connect()) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO broadcasts (broadcast_type, target_user_id, source_chat_id, source_message_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (broadcast_type, target_user_id, source_chat_id, source_message_id, now),
        )
        return cursor.lastrowid


def _update_result_sync(broadcast_id: int, status: str, sent_count: int, failed_count: int, completed_at: float) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            UPDATE broadcasts SET status = ?, sent_count = ?, failed_count = ?, completed_at = ?
            WHERE id = ?
            """,
            (status, sent_count, failed_count, completed_at, broadcast_id),
        )


def _get_broadcast_sync(broadcast_id: int):
    with closing(_connect()) as conn:
        return conn.execute("SELECT * FROM broadcasts WHERE id = ?", (broadcast_id,)).fetchone()


async def create_broadcast(
    broadcast_type: str, target_user_id: int | None, source_chat_id: int, source_message_id: int
) -> int:
    return await asyncio.to_thread(
        _create_broadcast_sync, broadcast_type, target_user_id, source_chat_id, source_message_id, time.time()
    )


async def mark_broadcast_result(broadcast_id: int, status: str, sent_count: int, failed_count: int) -> None:
    await asyncio.to_thread(_update_result_sync, broadcast_id, status, sent_count, failed_count, time.time())


async def get_broadcast(broadcast_id: int) -> Broadcast | None:
    row = await asyncio.to_thread(_get_broadcast_sync, broadcast_id)
    return _row_to_broadcast(row) if row else None
