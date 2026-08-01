"""Pure (non-Telegram) logic for the admin group control panel: builds the
text shown for Statistics/User Info, performs block/unblock, and builds the
blocklist CSV export. Telegram wiring (buttons, callbacks, sending
messages/files) lives in telegram_bot.py — this module can be tested without
python-telegram-bot at all."""

import csv
import io
from datetime import datetime, timezone

from linkcleaner import settings_store, stats_store
from linkcleaner.ad_store import MAX_EXPIRE_HOURS, MIN_EXPIRE_HOURS

TOP_DOMAINS_LIMIT = 10
USER_TOP_DOMAINS_LIMIT = 5


def parse_user_id(text: str) -> int | None:
    """Parses a Telegram numeric user ID from admin input. Returns None if
    `text` isn't a plain positive integer."""
    text = text.strip()
    if not text.isdigit():
        return None
    return int(text)


async def resolve_broadcast_target(text: str) -> int | None:
    """Resolves admin input (a numeric user ID, or an @username of someone
    who has messaged the bot before) to a user ID. Returns None if it can't
    be resolved."""
    text = text.strip()
    if text.startswith("@"):
        return await stats_store.get_user_id_by_username(text)

    user_id = parse_user_id(text)
    if user_id is None:
        return None

    info = await stats_store.get_user_info(user_id)
    return user_id if info.is_known else None


def parse_expire_hours(text: str) -> int | None:
    """Parses an ad's auto-delete delay. Must be a plain integer between
    MIN_EXPIRE_HOURS and MAX_EXPIRE_HOURS inclusive."""
    text = text.strip()
    if not text.isdigit():
        return None
    hours = int(text)
    if not (MIN_EXPIRE_HOURS <= hours <= MAX_EXPIRE_HOURS):
        return None
    return hours


def validate_button_url(text: str) -> str | None:
    """Accepts a button URL only if it's a plain http(s) link with no
    surrounding whitespace/garbage. Returns the URL unchanged if valid."""
    text = text.strip()
    if " " in text or "\n" in text:
        return None
    if not (text.startswith("http://") or text.startswith("https://")):
        return None
    if len(text) < len("http://x.co"):
        return None
    return text


def build_ad_preview_text(
    button_text: str | None, button_url: str | None, expire_hours: int, pinned: bool = False
) -> str:
    lines = [
        "📢 Ad ready to send.",
        "",
        f"Auto-deletes after : {expire_hours}h",
        f"Pinned : {'Yes' if pinned else 'No'}",
    ]
    if button_text and button_url:
        lines.append(f"Button : {button_text} → {button_url}")
    else:
        lines.append("Button : (none)")
    lines.append("")
    lines.append("Tap ✅ Send to broadcast it now, or ❌ Cancel to discard it.")
    return "\n".join(lines)


def validate_maintenance_message(text: str) -> str | None:
    """Accepts a non-empty maintenance message up to the configured
    character limit. Returns the trimmed message if valid, else None."""
    text = text.strip()
    if not text:
        return None
    if len(text) > settings_store.MAX_MAINTENANCE_MESSAGE_LENGTH:
        return None
    return text


def build_maintenance_status_text(state: settings_store.MaintenanceState) -> str:
    status = "🟢 ON — the bot is not responding to regular users" if state.enabled else "⚪ OFF — the bot is running normally"
    return (
        "🔧 Maintenance\n\n"
        f"Status : {status}\n\n"
        f"Current message shown to users while ON:\n{state.message}"
    )


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

    if info.privacy_mode:
        lines.append("🔒 This user has Privacy Mode enabled — their link-cleaning history is hidden.")
    else:
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
