import asyncio
import ipaddress
import logging
import os
import re
import socket
import time
from collections import defaultdict, deque
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Only respond in private chats. Bot intentionally does nothing in groups.
# ---------------------------------------------------------------------------
PRIVATE_ONLY = filters.ChatType.PRIVATE

# ---------------------------------------------------------------------------
# URL matching
# ---------------------------------------------------------------------------
URL_REGEX = re.compile(r"https?://[^\s]+")
TRAILING_CHARS = ".,!?:;'\">"

# ---------------------------------------------------------------------------
# Tracking parameters that are stripped regardless of domain
# ---------------------------------------------------------------------------
GENERIC_TRACKING_PARAMS_EXACT = {
    "fbclid", "mibextid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid",
    "gad_source", "msclkid", "ttclid", "twclid", "yclid", "ysclid",
    "igsh", "igshid", "si", "ncid", "cmpid", "icid", "ito",
    "mc_cid", "mc_eid", "mkt_tok", "vero_id", "_hsenc", "_hsmi",
    "hsctatracking", "elqtrackid", "oly_enc_id", "oly_anon_id",
    "ref", "ref_src", "ref_url", "spm", "scm",
}

GENERIC_TRACKING_PARAMS_PREFIX = (
    "utm_", "pf_rd_", "pd_rd_", "__cft__", "__tn__",
)

# ---------------------------------------------------------------------------
# Per-platform extra tracking parameters and whether to drop the fragment.
# Matched against the request host, including subdomains (e.g. m.youtube.com
# matches the "youtube.com" rule).
# ---------------------------------------------------------------------------
PLATFORM_RULES = [
    (
        frozenset({"facebook.com", "fb.com", "fb.watch", "fb.me", "messenger.com", "m.me"}),
        frozenset({
            "fbclid", "mibextid", "__tn__", "refsrc", "source", "extid",
            "paipv", "eav", "notif_id", "notif_t", "ref_component", "actorid", "hrc",
        }),
        True,
    ),
    (
        frozenset({"youtube.com", "youtu.be", "music.youtube.com"}),
        frozenset({"si", "feature", "ab_channel", "pp", "kw"}),
        False,
    ),
    (
        frozenset({"twitter.com", "x.com", "t.co"}),
        frozenset({"s", "t", "src"}),
        False,
    ),
    (
        frozenset({"instagram.com", "instagr.am"}),
        frozenset({"igsh", "igshid"}),
        False,
    ),
    (
        frozenset({"tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}),
        frozenset({
            "share_app_id", "checksum", "sender_device", "sender_web_id",
            "tt_from", "is_from_webapp", "is_copy_url", "u_code",
            "share_item_id", "source", "enter_from", "_t", "_r", "ttclid",
        }),
        True,
    ),
    (
        frozenset({"linkedin.com", "lnkd.in"}),
        frozenset({
            "trk", "trkcampaign", "trkemail", "rcm", "midtoken",
            "midsig", "originalsubdomain", "lipi", "otptoken", "eid",
        }),
        False,
    ),
    (
        frozenset({"snapchat.com", "story.snapchat.com"}),
        frozenset({"share_id", "sc_cid", "attributionid"}),
        False,
    ),
    (
        frozenset({"reddit.com", "redd.it"}),
        frozenset({"share_id"}),
        False,
    ),
    (
        frozenset({"pinterest.com", "pin.it"}),
        frozenset({"sender", "sender_id", "invite_code", "share_id"}),
        False,
    ),
    (
        frozenset({"amazon.com", "amazon.in", "amazon.co.uk", "amazon.de"}),
        frozenset({
            "tag", "ref_", "linkcode", "camp", "creative",
            "creativeasin", "psc", "spla", "keywords_id",
        }),
        False,
    ),
    (
        frozenset({"google.com"}),
        frozenset({"ved", "uact", "sxsrf", "ei", "sa", "gs_lcrp", "g_ep", "g_st"}),
        False,
    ),
    (
        frozenset({"spotify.com", "open.spotify.com", "spotify.link"}),
        frozenset({"si", "nd"}),
        False,
    ),
]


def find_platform_rule(domain: str):
    for domains, params, strip_fragment in PLATFORM_RULES:
        if domain in domains or any(domain.endswith("." + base) for base in domains):
            return params, strip_fragment
    return frozenset(), False


def strip_trailing_punctuation(url: str) -> str:
    while url and url[-1] in TRAILING_CHARS:
        url = url[:-1]
    if url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    return url


def clean_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url

    if not parsed.scheme or not parsed.netloc:
        return url

    domain = parsed.netloc.lower().removeprefix("www.")
    domain_extra_params, strip_fragment = find_platform_rule(domain)

    cleaned_params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in GENERIC_TRACKING_PARAMS_EXACT:
            continue
        if key_lower in domain_extra_params:
            continue
        if any(key_lower.startswith(prefix) for prefix in GENERIC_TRACKING_PARAMS_PREFIX):
            continue
        cleaned_params.append((key, value))

    new_query = urlencode(cleaned_params, doseq=True)
    fragment = "" if strip_fragment else parsed.fragment

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, fragment))


# ---------------------------------------------------------------------------
# SSRF-safe redirect resolver.
#
# User-supplied links are fetched server-side, which is a classic SSRF
# vector (e.g. a shortened link that redirects to http://169.254.169.254/
# or to an internal service on 10.x/172.16.x/192.168.x). To defend against
# that we:
#   1. only allow http/https schemes
#   2. resolve the DNS name of every hop ourselves and reject it if any
#      resolved IP is private, loopback, link-local, reserved or multicast
#   3. follow redirects manually (one hop at a time) instead of letting
#      the HTTP client auto-follow them, re-validating every hop
#   4. never read the response body (streaming, closed immediately), so a
#      malicious/huge response can't be used to exhaust memory or bandwidth
# ---------------------------------------------------------------------------
class UnsafeURLError(Exception):
    """Raised when a URL (or one of its redirect hops) is not safe to fetch."""


MAX_REDIRECTS = 5
RESOLVE_TIMEOUT = 8.0
ALLOWED_SCHEMES = {"http", "https"}
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _assert_host_is_public(host: str) -> None:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"could not resolve host: {host}") from exc

    ips = {info[4][0] for info in infos}
    if not ips:
        raise UnsafeURLError(f"no IP addresses found for host: {host}")
    for ip in ips:
        if _is_blocked_ip(ip):
            raise UnsafeURLError(f"blocked internal/private address for {host}: {ip}")


async def _assert_url_is_safe(url: str) -> None:
    parsed = httpx.URL(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"blocked scheme: {parsed.scheme}")
    if not parsed.host:
        raise UnsafeURLError("URL has no host")
    await _assert_host_is_public(parsed.host)


async def resolve_final_url(url: str) -> str:
    """Follow redirects one hop at a time (SSRF-validated at every hop) and
    return the final destination URL. Falls back to the last known-safe URL
    if anything looks unsafe, times out, or otherwise fails."""
    current = url
    last_safe = url
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=RESOLVE_TIMEOUT,
            headers=REQUEST_HEADERS,
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                await _assert_url_is_safe(current)
                last_safe = current
                async with client.stream("GET", current) as response:
                    location = response.headers.get("location")
                    if response.status_code in REDIRECT_STATUS_CODES and location:
                        current = str(httpx.URL(current).join(location))
                        continue
                    return str(response.url)
    except UnsafeURLError as exc:
        logger.warning("Blocked unsafe URL while resolving %s: %s", url, exc)
        return last_safe
    except httpx.HTTPError as exc:
        logger.warning("Could not resolve %s: %s", url, exc)
        return last_safe
    return last_safe


async def process_url(raw_url: str) -> str:
    url = strip_trailing_punctuation(raw_url)
    resolved = await resolve_final_url(url)
    return clean_url(resolved)


# ---------------------------------------------------------------------------
# Simple in-memory per-user rate limit (sliding window)
# ---------------------------------------------------------------------------
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 8

_user_request_times: dict[int, deque] = defaultdict(deque)


def is_rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    timestamps = _user_request_times[user_id]
    while timestamps and now - timestamps[0] > RATE_LIMIT_WINDOW_SECONDS:
        timestamps.popleft()
    if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
        return True
    timestamps.append(now)
    return False


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Send me any social media share link and I'll strip the tracking "
        "parameters and give you back a clean link.\n\n"
        "Works with shortened links too (bit.ly, vm.tiktok.com, etc.), "
        "I follow the redirect first, then clean it.\n\n"
        "Supported: Facebook, Messenger, YouTube, X/Twitter, Instagram, "
        "TikTok, LinkedIn, Snapchat, Reddit, Pinterest, Amazon, Google "
        "Search/Maps, Spotify, and generic utm_* trackers everywhere else."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


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

    cleaned_urls = await asyncio.gather(*(process_url(u) for u in raw_urls))

    if len(cleaned_urls) == 1:
        reply = cleaned_urls[0]
    else:
        reply = "\n".join(f"{i}. {u}" for i, u in enumerate(cleaned_urls, start=1))

    await message.reply_text(reply, disable_web_page_preview=False)


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
    app.add_handler(
        MessageHandler(
            PRIVATE_ONLY & ((filters.TEXT & ~filters.COMMAND) | filters.CAPTION),
            handle_message,
        )
    )
    app.add_error_handler(error_handler)

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
