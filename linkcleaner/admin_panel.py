"""Pure (non-Telegram) logic for the admin group control panel: builds the
text shown for Statistics/User Info, performs block/unblock, and builds the
blocklist CSV export. Telegram wiring (buttons, callbacks, sending
messages/files) lives in telegram_bot.py — this module can be tested without
python-telegram-bot at all."""

import csv
import io
from datetime import datetime, timezone

from linkcleaner import stats_store

TOP_DOMAINS_LIMIT = 10
USER_TOP_DOMAINS_LIMIT = 5


def parse_user_id(text: str) -> int | None:
    """Parses a Telegram numeric user ID from admin input. Returns None if
    `text` isn't a plain positive integer."""
    text = text.strip()
    if not text.isdigit():
        return None
    return int(text)


def _format_timestamp(ts: float | None) -> str:
    if ts is None:
        return "unknown"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _format_stats_block(stats) -> list[str]:
    return [
        "📊 Links Cleaned",
        f"Total : {stats.total}",
        f"Today (last 24h) : {stats.today}",
        f"This week (last 7d) : {stats.week}",
        f"This month (last 30d) : {stats.month}",
    ]


def _format_domain_list(title: str, domains: list[stats_store.DomainCount]) -> list[str]:
    if not domains:
        return []
    lines = ["", title]
    for i, dc in enumerate(domains, start=1):
        lines.append(f"{i}. {dc.domain} — {dc.count}")
    return lines


async def build_statistics_text() -> str:
    stats = await stats_store.get_global_stats()
    top_domains = await stats_store.get_top_domains(limit=TOP_DOMAINS_LIMIT)

    lines = _format_stats_block(stats)
    lines += _format_domain_list("Popular Domain", top_domains)
    return "\n".join(lines)


async def build_user_info_text(user_id: int) -> str:
    info = await stats_store.get_user_info(user_id)

    if not info.is_known:
        return f"No record found for user ID {user_id}. They may have never messaged the bot."

    username_line = f"@{info.username}" if info.username else "(none)"
    status_line = "🚫 Blocked" if info.blocked else "✅ Active"

    lines = [
        f"👤 User Info — {user_id}",
        "",
        f"Name : {info.first_name or '(unknown)'}",
        f"Username : {username_line}",
        f"Status : {status_line}",
        f"Member since : {_format_timestamp(info.first_seen)}",
        "",
    ]
    lines += _format_stats_block(info.stats)
    lines += _format_domain_list("Top Domains", info.top_domains)
    return "\n".join(lines)


async def block_user_text(user_id: int) -> str:
    await stats_store.block_user(user_id)
    return f"🚫 User {user_id} has been blocked."


async def unblock_user_text(user_id: int) -> str:
    await stats_store.unblock_user(user_id)
    return f"✅ User {user_id} has been unblocked."


async def build_blocklist_csv_bytes() -> bytes:
    blocked_users = await stats_store.list_blocked_users()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["user_id", "username", "first_name", "blocked_at_utc"])
    for u in blocked_users:
        writer.writerow([u.user_id, u.username or "", u.first_name or "", _format_timestamp(u.blocked_at)])

    return buffer.getvalue().encode("utf-8")
