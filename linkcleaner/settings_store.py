"""Bot-wide settings, backed by SQLite (same DB file as stats_store).

Currently just maintenance mode: a single on/off flag plus the message
shown to regular users while it's on. There's only ever one bot to
configure, so this is a single-row table (id=1).
"""

import asyncio
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass

DB_PATH = os.environ.get("STATS_DB_PATH", "linkcleaner_stats.db")

MAX_MAINTENANCE_MESSAGE_LENGTH = 2000
DEFAULT_MAINTENANCE_MESSAGE = "🔧 The bot is currently under maintenance. Please check back later."

_SETTINGS_ROW_ID = 1


@dataclass
class MaintenanceState:
    enabled: bool
    message: str


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            maintenance_enabled INTEGER NOT NULL DEFAULT 0,
            maintenance_message TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO bot_settings (id, maintenance_enabled, maintenance_message) VALUES (?, 0, ?)",
        (_SETTINGS_ROW_ID, DEFAULT_MAINTENANCE_MESSAGE),
    )
    return conn


def _get_state_sync() -> MaintenanceState:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT maintenance_enabled, maintenance_message FROM bot_settings WHERE id = ?",
            (_SETTINGS_ROW_ID,),
        ).fetchone()
    return MaintenanceState(enabled=bool(row[0]), message=row[1])


def _set_enabled_sync(enabled: bool) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE bot_settings SET maintenance_enabled = ? WHERE id = ?",
            (1 if enabled else 0, _SETTINGS_ROW_ID),
        )


def _set_message_sync(message: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE bot_settings SET maintenance_message = ? WHERE id = ?",
            (message, _SETTINGS_ROW_ID),
        )


async def get_maintenance_state() -> MaintenanceState:
    return await asyncio.to_thread(_get_state_sync)


async def set_maintenance_enabled(enabled: bool) -> None:
    await asyncio.to_thread(_set_enabled_sync, enabled)


async def set_maintenance_message(message: str) -> None:
    await asyncio.to_thread(_set_message_sync, message[:MAX_MAINTENANCE_MESSAGE_LENGTH])
