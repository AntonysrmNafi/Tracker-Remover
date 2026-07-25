"""Telegram handlers and application wiring. This is the only module that
talks to python-telegram-bot; everything else in the package is pure link
processing and can be tested without Telegram at all."""

import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

from linkcleaner import stats_store
from linkcleaner.link_processor import format_reply, process_url
from linkcleaner.profile import get_profile_text
from linkcleaner.rate_limiter import is_rate_limited
from linkcleaner.url_utils import URL_REGEX

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Only respond in private chats. Bot intentionally does nothing in groups.
PRIVATE_ONLY = filters.ChatType.PRIVATE

PROFILE_CALLBACK_DATA = "show_profile"
PROFILE_KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("👤 Profile", callback_data=PROFILE_CALLBACK_DATA)]]
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is not None:
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

    if is_rate_limited(message.from_user.id):
        await message.reply_text(
            "You're sending links too fast. Please wait a bit and try again."
        )
        return

    results = await asyncio.gather(*(process_url(u) for u in raw_urls))
    reply = format_reply(results)

    await message.reply_text(reply, disable_web_page_preview=True)

    user = message.from_user
    await stats_store.touch_user(user.id, user.username, user.first_name)
    await stats_store.record_links_cleaned(user.id, len(results))


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

    app.add_handler(CommandHandler("start", start, filters=PRIVATE_ONLY))
    app.add_handler(CommandHandler("help", help_command, filters=PRIVATE_ONLY))
    app.add_handler(CallbackQueryHandler(show_profile, pattern=f"^{PROFILE_CALLBACK_DATA}$"))
    app.add_handler(
        MessageHandler(
            PRIVATE_ONLY & ((filters.TEXT & ~filters.COMMAND) | filters.CAPTION),
            handle_message,
        )
    )
    app.add_error_handler(error_handler)

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()

