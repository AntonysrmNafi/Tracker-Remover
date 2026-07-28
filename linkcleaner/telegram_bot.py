"""Telegram handlers and application wiring. This is the only module that
dispatches python-telegram-bot updates to handlers; the actual sending
logic for broadcasts/ads lives in broadcast_service.py/ad_service.py so
this file stays focused on wiring and conversation-state bookkeeping."""

import asyncio
import io
import logging
import os
import time

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

from linkcleaner import ad_service, ad_store, admin_panel, broadcast_service, broadcast_store, stats_store
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
# Admin panel keyboards
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

BROADCAST_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 Public Broadcast", callback_data="admin:broadcast_public")],
    [InlineKeyboardButton("🎯 Specific User Broadcast", callback_data="admin:broadcast_specific")],
    [InlineKeyboardButton("⬅️ Back", callback_data="admin:broadcast_back")],
])

AD_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ Create a AD", callback_data="admin:ad_create")],
    [InlineKeyboardButton("⬅️ Back", callback_data="admin:ad_back")],
])

AD_BUTTON_CHOICE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ Add Button", callback_data="admin:ad_add_button")],
    [InlineKeyboardButton("⏭ Skip", callback_data="admin:ad_skip_button")],
    [InlineKeyboardButton("❌ Cancel & Home", callback_data="admin:ad_cancel")],
])

AD_CONFIRM_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Send", callback_data="admin:ad_send"),
    InlineKeyboardButton("❌ Cancel", callback_data="admin:ad_cancel"),
]])

# callback_data actions that are implemented; anything else on the main
# admin keyboard replies "coming soon" (Maintenance, Backup, Terms — to be
# implemented later).
_IMPLEMENTED_ADMIN_ACTIONS = {
    "statistics", "user_control", "user_info", "block_user", "unblock_user", "export_blocklist",
    "broadcast", "broadcast_public", "broadcast_specific", "broadcast_back",
    "ad", "ad_create", "ad_back", "ad_add_button", "ad_skip_button", "ad_cancel", "ad_send",
}

# Per-admin conversation state for multi-step flows (Block/Unblock/User Info,
# Broadcast, AD creation). In-memory only: lost on restart, which just means
# the admin has to tap the button again — no real downside, since nothing
# here is destructive until a final confirm step.
_admin_state: dict[int, dict] = {}


def _is_admin_group(chat) -> bool:
    return ADMIN_GROUP_ID is not None and chat is not None and chat.id == ADMIN_GROUP_ID


# ---------------------------------------------------------------------------
# Regular user handlers
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user

    if _is_admin_group(chat):
        if user is not None:
            _admin_state.pop(user.id, None)
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
# Admin panel: callback (button tap) handler
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
    message = query.message

    # --- top-level buttons -------------------------------------------------
    if action == "statistics":
        text = await admin_panel.build_statistics_text()
        await message.reply_text(text)

    elif action == "user_control":
        await message.reply_text("User Control:", reply_markup=USER_CONTROL_KEYBOARD)

    elif action == "user_info":
        _admin_state[admin_id] = {"flow": "user_info"}
        await message.reply_text("Send the numeric Telegram user ID to look up.")

    elif action == "block_user":
        _admin_state[admin_id] = {"flow": "block_user"}
        await message.reply_text("Send the numeric user ID to block.")

    elif action == "unblock_user":
        _admin_state[admin_id] = {"flow": "unblock_user"}
        await message.reply_text("Send the numeric user ID to unblock.")

    elif action == "export_blocklist":
        csv_bytes = await admin_panel.build_blocklist_csv_bytes()
        document = InputFile(io.BytesIO(csv_bytes), filename="blocklist.csv")
        await message.reply_document(document=document)

    # --- broadcast submenu --------------------------------------------------
    elif action == "broadcast":
        _admin_state.pop(admin_id, None)
        await message.reply_text("Broadcast:", reply_markup=BROADCAST_KEYBOARD)

    elif action == "broadcast_back":
        _admin_state.pop(admin_id, None)
        await message.reply_text(ADMIN_WELCOME_TEXT, reply_markup=ADMIN_MAIN_KEYBOARD)

    elif action == "broadcast_public":
        _admin_state[admin_id] = {"flow": "broadcast", "mode": "public", "step": "content"}
        await message.reply_text(
            "Send the message (text, photo, video, anything) to broadcast to ALL users. "
            "It will be sent immediately once you send it here."
        )

    elif action == "broadcast_specific":
        _admin_state[admin_id] = {"flow": "broadcast", "mode": "specific", "step": "target"}
        await message.reply_text("Send the username (@username) or numeric user ID of the recipient.")

    # --- AD submenu ----------------------------------------------------------
    elif action == "ad":
        _admin_state.pop(admin_id, None)
        await message.reply_text("AD:", reply_markup=AD_KEYBOARD)

    elif action == "ad_back":
        _admin_state.pop(admin_id, None)
        await message.reply_text(ADMIN_WELCOME_TEXT, reply_markup=ADMIN_MAIN_KEYBOARD)

    elif action == "ad_create":
        _admin_state[admin_id] = {"flow": "ad", "step": "content"}
        await message.reply_text("Send the ad content (message, photo, or anything).")

    elif action == "ad_add_button":
        state = _admin_state.get(admin_id)
        if not state or state.get("flow") != "ad" or state.get("step") != "button_choice":
            await message.reply_text("That button has expired. Please start over from the AD menu.")
            return
        state["step"] = "button_name"
        await message.reply_text("Send the button name (label).")

    elif action == "ad_skip_button":
        state = _admin_state.get(admin_id)
        if not state or state.get("flow") != "ad" or state.get("step") != "button_choice":
            await message.reply_text("That button has expired. Please start over from the AD menu.")
            return
        state["step"] = "hours"
        await message.reply_text(
            f"After how many hours should this AD auto-delete? "
            f"Enter a whole number from {ad_store.MIN_EXPIRE_HOURS} to {ad_store.MAX_EXPIRE_HOURS}."
        )

    elif action == "ad_cancel":
        state = _admin_state.pop(admin_id, None)
        if state and state.get("flow") == "ad" and state.get("ad_id") is not None:
            await ad_store.mark_ad_cancelled(state["ad_id"])
        await message.reply_text("❌ Cancelled.", reply_markup=ADMIN_MAIN_KEYBOARD)

    elif action == "ad_send":
        state = _admin_state.get(admin_id)
        if not state or state.get("flow") != "ad" or state.get("step") != "confirm":
            await message.reply_text("That button has expired. Please start over from the AD menu.")
            return
        ad_id = state["ad_id"]
        hours = state["expire_hours"]
        _admin_state.pop(admin_id, None)

        sent_count = await ad_service.send_ad(context.bot, ad_id)
        if context.job_queue is not None:
            context.job_queue.run_once(
                _expire_ad_job, when=hours * 3600, data={"ad_id": ad_id}, name=f"expire_ad_{ad_id}"
            )
        await message.reply_text(f"✅ AD sent to {sent_count} users. Will auto-delete in {hours}h.")

    elif action not in _IMPLEMENTED_ADMIN_ACTIONS:
        await message.reply_text("🚧 This feature is coming soon.")


# ---------------------------------------------------------------------------
# Admin panel: plain-text follow-up handler (numeric IDs, ad content, etc.)
# ---------------------------------------------------------------------------
async def handle_admin_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None or message.from_user is None:
        return

    admin_id = message.from_user.id
    state = _admin_state.get(admin_id)
    if state is None:
        return  # not something we asked this admin for; ignore

    flow = state.get("flow")

    if flow in ("user_info", "block_user", "unblock_user"):
        await _handle_simple_id_flow(message, admin_id, flow)
    elif flow == "broadcast":
        await _handle_broadcast_message(message, context, admin_id, state)
    elif flow == "ad":
        await _handle_ad_message(message, admin_id, state)


async def _handle_simple_id_flow(message, admin_id: int, flow: str) -> None:
    _admin_state.pop(admin_id, None)

    user_id = admin_panel.parse_user_id(message.text or "")
    if user_id is None:
        await message.reply_text("That doesn't look like a valid numeric user ID. Please tap the button and try again.")
        return

    if flow == "user_info":
        reply = await admin_panel.build_user_info_text(user_id)
    elif flow == "block_user":
        reply = await admin_panel.block_user_text(user_id)
    else:
        reply = await admin_panel.unblock_user_text(user_id)

    await message.reply_text(reply)


async def _handle_broadcast_message(message, context: ContextTypes.DEFAULT_TYPE, admin_id: int, state: dict) -> None:
    step = state.get("step")
    mode = state.get("mode")

    if step == "target":
        target_user_id = await admin_panel.resolve_broadcast_target(message.text or "")
        if target_user_id is None:
            _admin_state.pop(admin_id, None)
            await message.reply_text(
                "Could not find that user. They need to have used the bot before. "
                "Please tap the button and try again."
            )
            return
        state["target_user_id"] = target_user_id
        state["step"] = "content"
        await message.reply_text("Now send the message (text, photo, video, anything) to send to this user.")
        return

    if step == "content":
        source_chat_id = message.chat.id
        source_message_id = message.message_id
        _admin_state.pop(admin_id, None)

        if mode == "public":
            user_count = len(await stats_store.get_all_user_ids())
            broadcast_id = await broadcast_store.create_broadcast("public", None, source_chat_id, source_message_id)
            await message.reply_text(f"📢 Broadcasting to {user_count} users...")
            sent, failed = await broadcast_service.send_public_broadcast(
                context.bot, broadcast_id, source_chat_id, source_message_id
            )
            await message.reply_text(f"✅ Broadcast complete. Sent: {sent}, Failed: {failed}.")
        else:
            target_user_id = state["target_user_id"]
            broadcast_id = await broadcast_store.create_broadcast(
                "specific", target_user_id, source_chat_id, source_message_id
            )
            ok = await broadcast_service.send_specific_broadcast(
                context.bot, broadcast_id, target_user_id, source_chat_id, source_message_id
            )
            if ok:
                await message.reply_text(f"✅ Sent to user {target_user_id}.")
            else:
                await message.reply_text(
                    f"❌ Could not deliver to user {target_user_id} (they may have blocked the bot)."
                )


async def _handle_ad_message(message, admin_id: int, state: dict) -> None:
    step = state.get("step")

    if step == "content":
        ad_id = await ad_store.create_ad(message.chat.id, message.message_id)
        state["ad_id"] = ad_id
        state["step"] = "button_choice"
        await message.reply_text("Do you want to add a button?", reply_markup=AD_BUTTON_CHOICE_KEYBOARD)
        return

    if step == "button_name":
        button_text = (message.text or "").strip()
        if not button_text:
            await message.reply_text("Please send a non-empty button name.")
            return
        state["button_text"] = button_text
        state["step"] = "button_link"
        await message.reply_text("Send the button link (URL).")
        return

    if step == "button_link":
        button_url = admin_panel.validate_button_url(message.text or "")
        if button_url is None:
            await message.reply_text("That doesn't look like a valid link. It must start with http:// or https://.")
            return
        await ad_store.update_ad_button(state["ad_id"], state["button_text"], button_url)
        state["button_url"] = button_url
        state["step"] = "hours"
        await message.reply_text(
            f"After how many hours should this AD auto-delete? "
            f"Enter a whole number from {ad_store.MIN_EXPIRE_HOURS} to {ad_store.MAX_EXPIRE_HOURS}."
        )
        return

    if step == "hours":
        hours = admin_panel.parse_expire_hours(message.text or "")
        if hours is None:
            await message.reply_text(
                f"Please send a whole number from {ad_store.MIN_EXPIRE_HOURS} to {ad_store.MAX_EXPIRE_HOURS}."
            )
            return
        await ad_store.update_ad_expire_hours(state["ad_id"], hours)
        state["expire_hours"] = hours
        state["step"] = "confirm"
        preview = admin_panel.build_ad_preview_text(state.get("button_text"), state.get("button_url"), hours)
        await message.reply_text(preview, reply_markup=AD_CONFIRM_KEYBOARD)
        return


# ---------------------------------------------------------------------------
# Ad expiry job
# ---------------------------------------------------------------------------
async def _expire_ad_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    ad_id = context.job.data["ad_id"]
    await ad_service.expire_ad(context.bot, ad_id)


async def _reschedule_pending_ad_expiries(app: Application) -> None:
    if app.job_queue is None:
        return
    pending_ads = await ad_store.get_pending_expiry_ads()
    now = time.time()
    for ad in pending_ads:
        delay = max(0.0, ad.expires_at - now)
        app.job_queue.run_once(_expire_ad_job, when=delay, data={"ad_id": ad.id}, name=f"expire_ad_{ad.id}")
    if pending_ads:
        logger.info("Rescheduled %d pending ad expiry job(s) after startup.", len(pending_ads))


async def _post_init(app: Application) -> None:
    await _reschedule_pending_ad_expiries(app)


# ---------------------------------------------------------------------------
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
        .post_init(_post_init)
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
                filters.Chat(chat_id=ADMIN_GROUP_ID) & ~filters.COMMAND,
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
