"""Telegram handlers and application wiring. This is the only module that
talks to python-telegram-bot; everything else in the package is pure link
processing and can be tested without Telegram at all."""

import asyncio
import io
import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from linkcleaner import admin_panel, stats_store
from linkcleaner.link_processor import format_reply, process_url
from linkcleaner.profile import get_profile_text
from linkcleaner.rate_limiter import is_rate_limited
from linkcleaner.url_utils import URL_REGEX, get_domain

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Only respond in private chats. Bot intentionally does nothing in groups,
# except for the one designated admin group (see ADMIN_GROUP_ID below).
PRIVATE_ONLY = filters.ChatType.PRIVATE

_admin_group_env = os.environ.get("ADMIN_GROUP")
ADMIN_GROUP_ID = int(_admin_group_env) if _admin_group_env else None

PROFILE_CALLBACK_DATA = "show_profile"
PROFILE_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("👤 Profile", callback_data=PROFILE_CALLBACK_DATA)]]
)

BLOCKED_MESSAGE = "🚫 You are blocked from using this bot."

# ---------------------------------------------------------------------------
# Admin panel: 8 buttons, only Statistics / User Control / User Info are
# wired up so far — the rest reply "coming soon" until implemented.
# ---------------------------------------------------------------------------
ADMIN_WELCOME_TEXT = "🛠 Admin Control Panel\n\nUse the buttons below to manage the bot."

ADMIN_MAIN_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📊 Statistics", callback_data="admin:statistics"),
        InlineKeyboardButton("👥 User Control", callback_data="admin:user_control"),
    ],
    [
        InlineKeyboardButton("ℹ️ User Info", callback_data="admin:user_info"),
        InlineKeyboardButton("📢 AD", callback_data="admin:ad"),
    ],
    [
        InlineKeyboardButton("📣 Broadcast", callback_data="admin:broadcast"),
        InlineKeyboardButton("🔧 Maintenance", callback_data="admin:maintenance"),
    ],
    [
        InlineKeyboardButton("💾 Backup", callback_data="admin:backup"),
        InlineKeyboardButton("📜 Terms", callback_data="admin:terms"),
    ],
])

USER_CONTROL_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔒 Block User", callback_data="admin:block_user")],
    [InlineKeyboardButton("🔓 Unblock User", callback_data="admin:unblock_user")],
    [InlineKeyboardButton("📤 Export Blocklist (CSV)", callback_data="admin:export_blocklist")],
])

# callback_data actions that are implemented; anything else on the main
# admin keyboard replies "coming soon" (AD, Broadcast, Maintenance, Backup,
# Terms — to be implemented later).
_IMPLEMENTED_ADMIN_ACTIONS = {
    "statistics", "user_control", "user_info", "block_user", "unblock_user", "export_blocklist",
}

# Which numeric-ID reply an admin's next text message resolves to, keyed by
# the admin's Telegram user ID. In-memory only: lost on restart, which just
# means the admin has to tap the button again — no real downside.
_pending_admin_action: dict[int, str] = {}


def _is_admin_group(chat) -> bool:
    return ADMIN_GROUP_ID is not None and chat is not None and chat.id == ADMIN_GROUP_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user

    if _is_admin_group(chat):
        await update.message.reply_text(ADMIN_WELCOME_TEXT, reply_markup=ADMIN_MAIN_KEYBOARD)
        return

    if user is not None:
        if await stats_store.is_blocked(user.id):
            await update.message.reply_text(BLOCKED_MESSAGE)
            return
        await stats_store.touch_user(user.id, user.username, user.first_name)

    await update.message.reply_text(
        "Send me any social media share link and I'll strip the tracking "
        "parameters and give you back a clean link.\n\n"
        "Works with shortened links too (bit.ly, vm.tiktok.com, etc.), "
        "I follow the redirect first, then clean it.\n\n"
        "Supported: Facebook, Messenger, YouTube, X/Twitter, Instagram, "
        "TikTok, LinkedIn, Snapchat, Reddit, Pinterest, Amazon, Google "
        "Search/Maps, Spotify, and generic utm_* trackers everywhere else.",
        reply_markup=PROFILE_KEYBOARD,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.from_user is None:
        return
    if query.message is not None and query.message.chat.type != ChatType.PRIVATE:
        return

    if await stats_store.is_blocked(query.from_user.id):
        await query.answer(BLOCKED_MESSAGE, show_alert=True)
        return

    await query.answer()

    user = query.from_user
    text = await get_profile_text(user.id, user.first_name, user.username)
    await query.message.reply_text(text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or message.from_user is None:
        return

    text = message.text or message.caption
    if not text:
        return

    raw_urls = URL_REGEX.findall(text)
    if not raw_urls:
        return

    user = message.from_user

    if await stats_store.is_blocked(user.id):
        await message.reply_text(BLOCKED_MESSAGE)
        return

    if is_rate_limited(user.id):
        await message.reply_text(
            "You're sending links too fast. Please wait a bit and try again."
        )
        return

    results = await asyncio.gather(*(process_url(u) for u in raw_urls))
    reply = format_reply(results)

    await message.reply_text(reply, disable_web_page_preview=True)

    domains = [d for d in (get_domain(r["cleaned"]) for r in results) if d]
    await stats_store.touch_user(user.id, user.username, user.first_name)
    await stats_store.record_links_cleaned(user.id, domains)


# ---------------------------------------------------------------------------
# Admin panel handlers
# ---------------------------------------------------------------------------
async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.message is None or query.from_user is None:
        return
    if not _is_admin_group(query.message.chat):
        return

    await query.answer()

    action = query.data.split(":", 1)[1] if query.data and ":" in query.data else ""
    admin_id = query.from_user.id

    if action == "statistics":
        text = await admin_panel.build_statistics_text()
        await query.message.reply_text(text)
    elif action == "user_control":
        await query.message.reply_text("User Control:", reply_markup=USER_CONTROL_KEYBOARD)
    elif action == "user_info":
        _pending_admin_action[admin_id] = "user_info"
        await query.message.reply_text("Send the numeric Telegram user ID to look up.")
    elif action == "block_user":
        _pending_admin_action[admin_id] = "block_user"
        await query.message.reply_text("Send the numeric user ID to block.")
    elif action == "unblock_user":
        _pending_admin_action[admin_id] = "unblock_user"
        await query.message.reply_text("Send the numeric user ID to unblock.")
    elif action == "export_blocklist":
        csv_bytes = await admin_panel.build_blocklist_csv_bytes()
        document = InputFile(io.BytesIO(csv_bytes), filename="blocklist.csv")
        await query.message.reply_document(document=document)
    elif action not in _IMPLEMENTED_ADMIN_ACTIONS:
        await query.message.reply_text("🚧 This feature is coming soon.")


async def handle_admin_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or message.from_user is None:
        return

    admin_id = message.from_user.id
    action = _pending_admin_action.pop(admin_id, None)
    if action is None:
        return  # not something we asked this admin for; ignore

    user_id = admin_panel.parse_user_id(message.text or "")
    if user_id is None:
        await message.reply_text("That doesn't look like a valid numeric user ID. Please tap the button and try again.")
        return

    if action == "user_info":
        reply = await admin_panel.build_user_info_text(user_id)
    elif action == "block_user":
        reply = await admin_panel.block_user_text(user_id)
    elif action == "unblock_user":
        reply = await admin_panel.unblock_user_text(user_id)
    else:
        return

    await message.reply_text(reply)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception while processing an update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(
                "Something went wrong while cleaning that link. Please try again."
            )
        except TelegramError:
            pass


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable is not set")

    app = (
        Application.builder()
        .token(token)
        .rate_limiter(AIORateLimiter())
        .build()
    )

    start_filter = PRIVATE_ONLY
    if ADMIN_GROUP_ID is not None:
        start_filter = PRIVATE_ONLY | filters.Chat(chat_id=ADMIN_GROUP_ID)

    app.add_handler(CommandHandler("start", start, filters=start_filter))
    app.add_handler(CommandHandler("help", help_command, filters=PRIVATE_ONLY))
    app.add_handler(CallbackQueryHandler(show_profile, pattern=f"^{PROFILE_CALLBACK_DATA}$"))
    app.add_handler(
        MessageHandler(
            PRIVATE_ONLY & ((filters.TEXT & ~filters.COMMAND) | filters.CAPTION),
            handle_message,
        )
    )

    if ADMIN_GROUP_ID is not None:
        app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern=r"^admin:"))
        app.add_handler(
            MessageHandler(
                filters.Chat(chat_id=ADMIN_GROUP_ID) & filters.TEXT & ~filters.COMMAND,
                handle_admin_group_message,
            )
        )
    else:
        logger.info("ADMIN_GROUP not set — admin control panel is disabled.")

    app.add_error_handler(error_handler)

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
