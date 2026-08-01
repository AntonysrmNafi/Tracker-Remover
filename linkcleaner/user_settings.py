"""Builds the text for the user-facing "⚙️ Settings" / "🔐 Privacy Mode"
views. Telegram wiring lives in telegram_bot.py."""

from linkcleaner import stats_store

SETTINGS_TEXT = "⚙️ Settings\n\nChoose what you'd like to configure."


def format_privacy_mode_text(enabled: bool) -> str:
    status = "🔒 ON" if enabled else "🔓 OFF"
    return (
        "🔐 Privacy Mode\n\n"
        f"Status : {status}\n\n"
        "When ON, the admin panel's User Info won't show your link-cleaning "
        "history or stats. Off by default."
    )


async def get_privacy_mode_text(user_id: int) -> str:
    enabled = await stats_store.is_privacy_mode_enabled(user_id)
    return format_privacy_mode_text(enabled)
