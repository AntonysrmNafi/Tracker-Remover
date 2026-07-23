import asyncio
import logging
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://[^\s]+")
TRAILING_CHARS = ".,!?:;'\">"

TRACKING_PARAMS_EXACT = {
    "fbclid", "mibextid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid",
    "gad_source", "msclkid", "igsh", "igshid", "si", "yclid", "ncid",
    "cmpid", "icid", "ito", "mc_cid", "mc_eid", "mkt_tok", "vero_id",
    "_hsenc", "_hsmi", "hsctatracking", "elqtrackid", "oly_enc_id",
    "oly_anon_id", "ref", "ref_src", "ref_url", "spm", "scm", "trk",
    "trkcampaign", "wt.mc_id", "share_app_id", "checksum", "sender_device",
    "sender_web_id", "tt_from", "is_from_webapp", "is_copy_url", "_t", "_r",
}

TRACKING_PARAMS_PREFIX = (
    "utm_", "pf_rd_", "pd_rd_", "__cft__", "__tn__",
)

DOMAIN_TRACKING_PARAMS = {
    "twitter.com": {"s", "t"},
    "x.com": {"s", "t"},
    "amazon.com": {"tag", "ref_", "linkcode", "camp", "creative", "creativeasin", "psc", "spla"},
    "amazon.in": {"tag", "ref_", "linkcode", "camp", "creative", "creativeasin", "psc", "spla"},
}

FRAGMENT_STRIP_DOMAINS = ("facebook.com", "fb.watch", "tiktok.com")

REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
RESOLVE_TIMEOUT = 8


def strip_trailing_punctuation(url: str) -> str:
    while url and url[-1] in TRAILING_CHARS:
        url = url[:-1]
    if url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    return url


def resolve_redirects(url: str) -> str:
    """Follow redirects synchronously to get the final destination URL.
    Falls back to the original URL if the request fails for any reason."""
    try:
        with requests.get(
            url,
            allow_redirects=True,
            timeout=RESOLVE_TIMEOUT,
            headers=REQUEST_HEADERS,
            stream=True,
        ) as response:
            return response.url or url
    except requests.RequestException as exc:
        logger.warning("Could not resolve %s: %s", url, exc)
        return url


def clean_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url

    if not parsed.scheme or not parsed.netloc:
        return url

    domain = parsed.netloc.lower().removeprefix("www.")
    domain_extra_params = DOMAIN_TRACKING_PARAMS.get(domain, set())

    cleaned_params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_PARAMS_EXACT:
            continue
        if key_lower in domain_extra_params:
            continue
        if any(key_lower.startswith(prefix) for prefix in TRACKING_PARAMS_PREFIX):
            continue
        cleaned_params.append((key, value))

    new_query = urlencode(cleaned_params, doseq=True)
    fragment = "" if any(d in domain for d in FRAGMENT_STRIP_DOMAINS) else parsed.fragment

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, fragment))


async def process_url(raw_url: str) -> str:
    url = strip_trailing_punctuation(raw_url)
    resolved = await asyncio.to_thread(resolve_redirects, url)
    return clean_url(resolved)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me any social media share link and I'll strip the tracking "
        "parameters and give you back a clean link.\n\n"
        "Works with shortened links too (bit.ly, vm.tiktok.com, etc.), "
        "I follow the redirect first, then clean it."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if message is None:
        return

    text = message.text or message.caption
    if not text:
        return

    raw_urls = URL_REGEX.findall(text)
    if not raw_urls:
        return

    cleaned_urls = await asyncio.gather(*(process_url(u) for u in raw_urls))

    if len(cleaned_urls) == 1:
        reply = cleaned_urls[0]
    else:
        reply = "\n".join(f"{i}. {u}" for i, u in enumerate(cleaned_urls, start=1))

    await message.reply_text(reply, disable_web_page_preview=False)


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable is not set")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(
        MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.CAPTION, handle_message)
    )

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
