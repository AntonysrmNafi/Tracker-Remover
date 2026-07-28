"""Sends broadcasts to users via Telegram's copy_message, which works for
any content type (text, photo, video, document, etc.) without needing to
inspect what kind of message it is. Talks to the Telegram Bot API — kept
separate from telegram_bot.py's handler-dispatch code to keep that file
manageable; telegram_bot.py still owns all button/callback wiring."""

import asyncio
import logging

from telegram import Bot
from telegram.error import TelegramError

from linkcleaner import broadcast_store, stats_store

logger = logging.getLogger(__name__)

# Telegram allows roughly 30 messages/second across all chats. Bounded
# concurrency keeps us comfortably under that while still finishing a
# broadcast to a large user base well within an hour.
MAX_CONCURRENT_SENDS = 20


async def _copy_to_one(
    bot: Bot, target_user_id: int, source_chat_id: int, source_message_id: int, semaphore: asyncio.Semaphore
) -> bool:
    async with semaphore:
        try:
            await bot.copy_message(
                chat_id=target_user_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
            )
            return True
        except TelegramError as exc:
            logger.warning("Broadcast to %s failed: %s", target_user_id, exc)
            return False


async def send_public_broadcast(bot: Bot, broadcast_id: int, source_chat_id: int, source_message_id: int) -> tuple[int, int]:
    """Sends the broadcast to every known user (blocked or not). Returns
    (sent_count, failed_count)."""
    user_ids = await stats_store.get_all_user_ids()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SENDS)
    results = await asyncio.gather(
        *(_copy_to_one(bot, uid, source_chat_id, source_message_id, semaphore) for uid in user_ids)
    )
    sent = sum(1 for ok in results if ok)
    failed = len(results) - sent
    status = "sent" if sent > 0 or not results else "failed"
    await broadcast_store.mark_broadcast_result(broadcast_id, status, sent, failed)
    return sent, failed


async def send_specific_broadcast(
    bot: Bot, broadcast_id: int, target_user_id: int, source_chat_id: int, source_message_id: int
) -> bool:
    semaphore = asyncio.Semaphore(1)
    ok = await _copy_to_one(bot, target_user_id, source_chat_id, source_message_id, semaphore)
    await broadcast_store.mark_broadcast_result(broadcast_id, "sent" if ok else "failed", 1 if ok else 0, 0 if ok else 1)
    return ok
