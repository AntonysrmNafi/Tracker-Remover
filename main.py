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
            "rdid", "share_url",
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


# ---------------------------------------------------------------------------
# Only fetch a link over the network if it's actually a wrapper/shortener
# that needs resolving. A full/direct link (e.g. a normal linkedin.com post
# URL, a full instagram.com/reel/... URL) is already the real destination,
# so it's cleaned in place without a network round trip. This also avoids a
# real bug: fetching an already-direct link can hit a platform's login/
# authwall page (LinkedIn does this for logged-out requests) and redirect to
# a generic homepage, destroying the real path for no reason.
# ---------------------------------------------------------------------------
SHORTENER_DOMAINS = frozenset({
    "bit.ly", "t.co", "lnkd.in", "vm.tiktok.com", "vt.tiktok.com",
    "fb.watch", "fb.me", "goo.gl", "amzn.to", "pin.it", "redd.it",
    "spotify.link",
    # General-purpose shorteners below. New ones launch constantly, so this
    # curated list is just a fast-path; the bare-short-path heuristic further
    # down is the real backstop for shorteners we've never seen before.
    "tinyurl.com", "is.gd", "ow.ly", "buff.ly", "shorturl.at", "rebrand.ly",
    "cutt.ly", "soo.gd", "tiny.cc", "rb.gy", "s.id", "bl.ink", "shrtco.de",
    "v.gd", "qr.ae", "tr.im", "adf.ly", "tny.im", "x.co", "cli.gs",
    "shorte.st", "po.st", "mcaf.ee", "ln.run", "git.io", "dub.sh", "t.ly",
    "snip.ly", "0rz.tw", "urlz.fr", "hyperurl.co", "chilp.it", "kutt.it",
    "gg.gg", "clck.ru", "u.to", "waa.ai", "zpr.io", "urlr.me", "shorturl.com",
    "shorturl.gg", "tiny.one", "smallurl.co", "rotf.lu", "urlz.de",
})

# Real-world shorteners aren't a closed set (new ones launch all the time),
# so alongside the curated list above we also treat "domain we don't
# recognize + a single short random-looking path segment + no query string"
# as a probable shortener and try to resolve it. Domains we already handle
# explicitly (via PLATFORM_RULES) are excluded so this can't re-trigger the
# LinkedIn-authwall-style bug on a platform we deliberately don't resolve.
_RECOGNIZED_PLATFORM_DOMAINS = frozenset(
    domain for domains, _params, _frag in PLATFORM_RULES for domain in domains
)
_GENERIC_SHORT_PATH_RE = re.compile(r"^/(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{4,12}/?$")


def _looks_like_unknown_shortlink(url: str, domain: str) -> bool:
    if domain in _RECOGNIZED_PLATFORM_DOMAINS:
        return False
    if any(domain.endswith("." + d) for d in _RECOGNIZED_PLATFORM_DOMAINS):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.query:
        return False
    return bool(_GENERIC_SHORT_PATH_RE.match(parsed.path))


def _is_known_shortener(domain: str, path: str) -> bool:
    if domain in SHORTENER_DOMAINS or any(domain.endswith("." + d) for d in SHORTENER_DOMAINS):
        return True
    # Facebook's /share/v/... and /share/r/... paths are wrapper links even
    # though they live on facebook.com itself; a direct facebook.com/reel/...
    # or facebook.com/watch?v=... link does not need resolving.
    if domain == "facebook.com" or domain.endswith(".facebook.com"):
        return path.startswith("/share/")
    return False


def needs_resolution(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False

    domain = parsed.netloc.lower().removeprefix("www.")
    if _is_known_shortener(domain, parsed.path):
        return True

    return _looks_like_unknown_shortlink(url, domain)


def strip_trailing_punctuation(url: str) -> str:
    while url and url[-1] in TRAILING_CHARS:
        url = url[:-1]
    if url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    return url


def _clean_url_internal(url: str) -> tuple[str, list[str]]:
    """Returns (cleaned_url, removed_param_names). If the URL can't be
    parsed, returns it unchanged with an empty removed-params list."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url, []

    if not parsed.scheme or not parsed.netloc:
        return url, []

    domain = parsed.netloc.lower().removeprefix("www.")
    domain_extra_params, strip_fragment = find_platform_rule(domain)

    cleaned_params = []
    removed_params: list[str] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if (
            key_lower in GENERIC_TRACKING_PARAMS_EXACT
            or key_lower in domain_extra_params
            or any(key_lower.startswith(prefix) for prefix in GENERIC_TRACKING_PARAMS_PREFIX)
        ):
            removed_params.append(key)
            continue
        cleaned_params.append((key, value))

    if strip_fragment and parsed.fragment:
        removed_params.append("fragment")

    new_query = urlencode(cleaned_params, doseq=True)
    fragment = "" if strip_fragment else parsed.fragment

    cleaned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, fragment))
    return cleaned, removed_params


def clean_url(url: str) -> str:
    cleaned, _removed_params = _clean_url_internal(url)
    return cleaned


def clean_url_with_trackers(url: str) -> tuple[str, list[str]]:
    return _clean_url_internal(url)


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


MAX_REDIRECTS = 6
RESOLVE_TIMEOUT = 8.0
ALLOWED_SCHEMES = {"http", "https"}
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
CANONICAL_URL_SCAN_LIMIT = 300_000  # bytes; only the <head> is needed, this is a generous cap
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Facebook's own /share/v/... and /share/r/... links frequently don't send a
# normal HTTP redirect (or a usable og:url tag) to generic bot traffic, but
# they do to known link-preview crawlers (this is how link previews work on
# Messenger, WhatsApp, Slack, Twitter, etc.). Impersonating that crawler UA
# for facebook.com hosts is a standard, widely used technique to reliably get
# the real og:url back instead of an interstitial page. Twitter/X does the
# same allowlisting for its own "Twitterbot" UA.
FACEBOOK_HOST_SUFFIXES = ("facebook.com", "fb.watch", "fb.com", "fb.me", "messenger.com", "m.me")
FACEBOOK_CRAWLER_HEADERS = {
    "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Accept": "*/*",
}

TWITTER_HOST_SUFFIXES = ("twitter.com", "x.com", "t.co")
TWITTER_CRAWLER_HEADERS = {
    "User-Agent": "Twitterbot/1.0",
    "Accept": "*/*",
}


def _headers_for(url: str) -> dict:
    host = (httpx.URL(url).host or "").lower()
    if any(host == suffix or host.endswith("." + suffix) for suffix in FACEBOOK_HOST_SUFFIXES):
        return FACEBOOK_CRAWLER_HEADERS
    if any(host == suffix or host.endswith("." + suffix) for suffix in TWITTER_HOST_SUFFIXES):
        return TWITTER_CRAWLER_HEADERS
    return {}


# Some platforms don't send a normal HTTP redirect to automated clients;
# instead they serve an HTML page holding the real destination URL in one of
# a few common places. These regexes look for each, in order of reliability,
# without needing a full HTML parser:
#   1. <meta property="og:url" content="...">
#   2. <link rel="canonical" href="...">
#   3. <meta http-equiv="refresh" content="0; url=...">  (old-style bounce page)
#   4. a JSON-escaped "url":"https:\/\/..." embedded in inline page data
#      (some platforms only expose the destination this way to non-JS clients)
OG_URL_RE = re.compile(
    r'<meta\b(?=[^>]*\bproperty\s*=\s*["\']og:url["\'])(?=[^>]*\bcontent\s*=\s*["\']([^"\']+)["\'])[^>]*>',
    re.IGNORECASE,
)
CANONICAL_LINK_RE = re.compile(
    r'<link\b(?=[^>]*\brel\s*=\s*["\']canonical["\'])(?=[^>]*\bhref\s*=\s*["\']([^"\']+)["\'])[^>]*>',
    re.IGNORECASE,
)
META_REFRESH_RE = re.compile(
    r'<meta\b(?=[^>]*\bhttp-equiv\s*=\s*["\']refresh["\'])'
    r'(?=[^>]*\bcontent\s*=\s*["\'][^"\']*url=([^"\'&]+))[^>]*>',
    re.IGNORECASE,
)
JSON_ESCAPED_URL_RE = re.compile(r'"url"\s*:\s*"(https:\\/\\/[^"]+)"', re.IGNORECASE)


async def _extract_canonical_url(response: httpx.Response) -> str | None:
    """Read up to CANONICAL_URL_SCAN_LIMIT bytes of an HTML response looking
    for the real destination URL. Capped so a huge/slow response can't be
    used to exhaust memory or bandwidth."""
    collected = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            collected.extend(chunk)
            if len(collected) >= CANONICAL_URL_SCAN_LIMIT:
                break
    except httpx.HTTPError:
        return None

    text = collected.decode("utf-8", errors="ignore")

    match = OG_URL_RE.search(text) or CANONICAL_LINK_RE.search(text) or META_REFRESH_RE.search(text)
    if match:
        return match.group(1)

    json_match = JSON_ESCAPED_URL_RE.search(text)
    if json_match:
        return json_match.group(1).replace("\\/", "/")

    return None


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


# Landing on one of these after following a redirect/canonical chain almost
# always means "you're not logged in" rather than "here's the content", e.g.
# LinkedIn redirecting an unauthenticated request for a post to its homepage
# or an authwall page. In that case the resolved URL is strictly worse than
# what the user gave us, so we keep the original instead of the redirect.
AUTHWALL_PATH_MARKERS = ("authwall", "checkpoint", "uas/login", "login", "signin", "consent")


def _guard_against_authwall(original_url: str, resolved_url: str) -> str:
    if resolved_url == original_url:
        return resolved_url

    original_path = urlsplit(original_url).path
    if original_path in ("", "/"):
        return resolved_url  # original had no real content path to protect

    resolved_path = urlsplit(resolved_url).path.lower()
    if resolved_path in ("", "/") or any(marker in resolved_path for marker in AUTHWALL_PATH_MARKERS):
        logger.info(
            "Resolved URL %s looks like a login/authwall page for %s, keeping the original",
            resolved_url, original_url,
        )
        return original_url

    return resolved_url


async def resolve_final_url(url: str, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Follow redirects one hop at a time (SSRF-validated at every hop) and
    return the final destination URL. Falls back to the last known-safe URL
    if anything looks unsafe, times out, or otherwise fails.

    `transport` is only used by tests to simulate HTTP responses without
    making real network calls; production code always uses the default
    (real) transport.
    """
    current = url
    last_safe = url
    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=RESOLVE_TIMEOUT,
            headers=REQUEST_HEADERS,
            transport=transport,
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                await _assert_url_is_safe(current)
                last_safe = current
                async with client.stream("GET", current, headers=_headers_for(current)) as response:
                    location = response.headers.get("location")
                    if response.status_code in REDIRECT_STATUS_CODES and location:
                        current = str(httpx.URL(current).join(location))
                        continue

                    canonical = None
                    content_type = response.headers.get("content-type", "")
                    if "text/html" in content_type.lower():
                        canonical = await _extract_canonical_url(response)

                    if canonical:
                        candidate = str(httpx.URL(current).join(canonical))
                        if candidate != current:
                            try:
                                await _assert_url_is_safe(candidate)
                            except UnsafeURLError:
                                return _guard_against_authwall(url, str(response.url))
                            current = candidate
                            continue

                    if str(response.url) == url:
                        logger.info(
                            "No redirect or canonical URL found for %s (status=%s, content-type=%s)",
                            url, response.status_code, content_type,
                        )
                    return _guard_against_authwall(url, str(response.url))
    except UnsafeURLError as exc:
        logger.warning("Blocked unsafe URL while resolving %s: %s", url, exc)
        return last_safe
    except httpx.HTTPError as exc:
        logger.warning("Could not resolve %s: %s", url, exc)
        return last_safe
    return last_safe


async def process_url(raw_url: str) -> dict:
    """Resolve + clean one URL and report what was done to it.

    Returns a dict with:
      original             the exact text the user sent (unmodified)
      cleaned               the resolved, tracker-free URL
      removed_params        tracking query params (and "fragment" if dropped)
      was_redirected        True if the link was a short/redirect link that
                             was successfully followed to a different URL
      attempted_resolution  True if this was a *confirmed* shortener (known
                             domain, or Facebook's /share/ wrapper) we tried
                             to resolve, whether or not it succeeded. A link
                             that only matched the generic short-path
                             heuristic and turned out to be a normal direct
                             link does not set this, so it doesn't get an
                             unnecessary "could not verify" message.
    """
    stripped = strip_trailing_punctuation(raw_url)
    parsed = urlsplit(stripped)
    domain = parsed.netloc.lower().removeprefix("www.")
    confirmed_shortener = _is_known_shortener(domain, parsed.path)

    if confirmed_shortener or _looks_like_unknown_shortlink(stripped, domain):
        resolved = await resolve_final_url(stripped)
    else:
        resolved = stripped
    cleaned, removed_params = clean_url_with_trackers(resolved)
    return {
        "original": raw_url,
        "cleaned": cleaned,
        "removed_params": removed_params,
        "was_redirected": resolved != stripped,
        "attempted_resolution": confirmed_shortener,
    }


def format_link_block(
    original: str,
    cleaned: str,
    removed_params: list[str],
    was_redirected: bool,
    attempted_resolution: bool = False,
) -> str:
    items = list(dict.fromkeys(removed_params))  # dedupe, keep first-seen order
    if was_redirected:
        items.append("Short URL (resolved)")
    elif attempted_resolution:
        items.append("Short URL (could not verify destination, kept original)")
    tracker_text = ", ".join(items) if items else "None found"

    return (
        f"Your Link : {original}\n"
        f"Clean & Secure Link : {cleaned}\n"
        f"Tracker : {tracker_text}"
    )


def format_reply(results: list[dict]) -> str:
    return "\n\n".join(format_link_block(**result) for result in results)



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

    results = await asyncio.gather(*(process_url(u) for u in raw_urls))
    reply = format_reply(results)

    await message.reply_text(reply, disable_web_page_preview=True)


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
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
