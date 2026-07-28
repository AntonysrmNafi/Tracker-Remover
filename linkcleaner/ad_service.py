"""Sends and expires ad campaigns via Telegram's copy_message/delete_message,
tracking per-recipient delivery so an ad can be auto-deleted from every
recipient's chat once it expires. Talks to the Telegram Bot API — kept
separate from telegram_bot.py's handler-dispatch code."""

import asyncio
import logging
import time

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from linkcleaner import ad_store, stats_store

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SENDS = 20


def _build_markup(button_text: str | None, button_url: str | None) -> InlineKeyboardMarkup | None:
    if not button_text or not button_url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(button_text, url=button_url)]])


async def _send_ad_to_one(
    bot: Bot,
    ad_id: int,
    user_id: int,
    source_chat_id: int,
    source_message_id: int,
    markup: InlineKeyboardMarkup | None,
    semaphore: asyncio.Semaphore,
) -> bool:
    async with semaphore:
        try:
            result = await bot.copy_message(
                chat_id=user_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id,
                reply_markup=markup,
            )
            await ad_store.record_delivery(ad_id, user_id, result.message_id)
            return True
        except TelegramError as exc:
            logger.warning("Ad %s delivery to %s failed: %s", ad_id, user_id, exc)
            return False


async def send_ad(bot: Bot, ad_id: int) -> int:
    """Sends the ad to every known user (blocked or not), records each
    delivery, and marks the ad as sent with its expiry time set. Returns
    the number of users it was successfully sent to."""
    ad = await ad_store.get_ad(ad_id)
    if ad is None:
        return 0

    markup = _build_markup(ad.button_text, ad.button_url)
    user_ids = await stats_store.get_all_user_ids()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SENDS)
    results = await asyncio.gather(
        *(
            _send_ad_to_one(bot, ad_id, uid, ad.source_chat_id, ad.source_message_id, markup, semaphore)
            for uid in user_ids
        )
    )
    sent = sum(1 for ok in results if ok)

    expires_at = time.time() + ad.expire_hours * 3600
    await ad_store.mark_ad_sent(ad_id, sent, expires_at)
    return sent


async def expire_ad(bot: Bot, ad_id: int) -> None:
    """Deletes the ad from every recipient's chat it's still present in,
    then marks the ad as expired."""
    deliveries = await ad_store.get_active_deliveries(ad_id)
    for delivery in deliveries:
        try:
            await bot.delete_message(chat_id=delivery.user_id, message_id=delivery.sent_message_id)
        except TelegramError as exc:
            logger.warning("Could not delete ad %s message for user %s: %s", ad_id, delivery.user_id, exc)
        await ad_store.mark_delivery_deleted(delivery.id)
    await ad_store.mark_ad_expired(ad_id)
