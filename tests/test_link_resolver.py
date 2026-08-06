"""Follows redirects (SSRF-validated at every hop, see linkcleaner.ssrf_guard)
to find a link's real destination.

Some platforms don't send a normal HTTP redirect to generic bot traffic —
Facebook's /share/v/... and /share/r/... links are the clearest example —
but do serve one to known link-preview crawlers, or embed the destination in
the HTML instead of redirecting at all. This module handles both cases:

  1. only http/https schemes are followed
  2. every hop is SSRF-validated before it's fetched
  3. redirects are followed manually (one hop at a time) instead of letting
     the HTTP client auto-follow them, so each hop gets re-validated
  4. the response body is streamed and capped, never fully downloaded
  5. platform-appropriate crawler User-Agents are used where that's the
     standard, documented way to get the real destination back
  6. if there's no HTTP redirect, a capped read of the HTML looks for the
     destination in an og:url tag, a canonical link, a meta-refresh, or an
     embedded JSON-escaped URL
  7. a resolved URL that looks like a login/authwall bounce is discarded in
     favor of the original link, so a working short link never gets replaced
     by something worse
"""

import logging
import re
from urllib.parse import urlsplit

import httpx

from linkcleaner.ssrf_guard import UnsafeURLError, assert_url_is_safe

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 6
RESOLVE_TIMEOUT = 8.0
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


def headers_for(url: str) -> dict:
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


async def extract_canonical_url(response: httpx.Response) -> str | None:
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


# Landing on one of these after following a redirect/canonical chain almost
# always means "you're not logged in" rather than "here's the content", e.g.
# LinkedIn redirecting an unauthenticated request for a post to its homepage
# or an authwall page. In that case the resolved URL is strictly worse than
# what the user gave us, so we keep the original instead of the redirect.
AUTHWALL_PATH_MARKERS = ("authwall", "checkpoint", "uas/login", "login", "signin", "consent")

# A bare "/" landing is ambiguous on its own: it's the authwall/anti-bot
# bounce symptom we've seen from LinkedIn and TikTok, but for most other
# sites a short link legitimately pointing at the homepage is completely
# normal (e.g. shorturl.at/xyz -> https://example.com/). So we only treat a
# bare "/" as suspicious for platforms we've actually observed doing this;
# everywhere else, the keyword check above is the only signal used.
BOUNCE_PRONE_DOMAINS = frozenset({"linkedin.com", "tiktok.com"})


def guard_against_authwall(original_url: str, resolved_url: str) -> str:
    if resolved_url == original_url:
        return resolved_url

    original_path = urlsplit(original_url).path
    if original_path in ("", "/"):
        return resolved_url  # original had no real content path to protect

    resolved_parsed = urlsplit(resolved_url)
    resolved_path = resolved_parsed.path.lower()
    resolved_domain = resolved_parsed.netloc.lower().removeprefix("www.")

    is_authwall_keyword = any(marker in resolved_path for marker in AUTHWALL_PATH_MARKERS)
    is_bounce_prone_homepage = resolved_path in ("", "/") and (
        resolved_domain in BOUNCE_PRONE_DOMAINS
        or any(resolved_domain.endswith("." + d) for d in BOUNCE_PRONE_DOMAINS)
    )

    if is_authwall_keyword or is_bounce_prone_homepage:
        logger.info(
            "Resolved URL %s looks like a login/authwall page for %s, keeping the original",
            resolved_url, original_url,
        )
        return original_url

    return resolved_url


FACEBOOK_WARMUP_URL = "https://www.facebook.com/"


async def _warm_up_facebook_cookies(client: httpx.AsyncClient, url: str) -> None:
    """Facebook increasingly login-walls video/reel content for requests
    that show up with zero prior cookies at all, even when the content is
    public — a real browser's very first visit already carries a baseline
    anonymous cookie (e.g. "datr") that Facebook itself set. This does the
    same anonymous warm-up: one throwaway GET to the Facebook homepage so
    the client's cookie jar picks up that baseline cookie before the real
    request. Best-effort — if it fails for any reason, resolution still
    proceeds without it."""
    host = (httpx.URL(url).host or "").lower()
    if not any(host == suffix or host.endswith("." + suffix) for suffix in FACEBOOK_HOST_SUFFIXES):
        return
    try:
        await assert_url_is_safe(FACEBOOK_WARMUP_URL)
        async with client.stream("GET", FACEBOOK_WARMUP_URL, headers=FACEBOOK_CRAWLER_HEADERS):
            pass
    except (UnsafeURLError, httpx.HTTPError) as exc:
        logger.warning("Facebook cookie warm-up failed (continuing without it): %s", exc)


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
            await _warm_up_facebook_cookies(client, url)
            for _ in range(MAX_REDIRECTS + 1):
                await assert_url_is_safe(current)
                last_safe = current
                async with client.stream("GET", current, headers=headers_for(current)) as response:
                    location = response.headers.get("location")
                    if response.status_code in REDIRECT_STATUS_CODES and location:
                        current = str(httpx.URL(current).join(location))
                        continue

                    canonical = None
                    content_type = response.headers.get("content-type", "")
                    if "text/html" in content_type.lower():
                        canonical = await extract_canonical_url(response)

                    if canonical:
                        candidate = str(httpx.URL(current).join(canonical))
                        if candidate != current:
                            try:
                                await assert_url_is_safe(candidate)
                            except UnsafeURLError:
                                return guard_against_authwall(url, str(response.url))
                            current = candidate
                            continue

                    if str(response.url) == url:
                        logger.info(
                            "No redirect or canonical URL found for %s (status=%s, content-type=%s)",
                            url, response.status_code, content_type,
                        )
                    return guard_against_authwall(url, str(response.url))
    except UnsafeURLError as exc:
        logger.warning("Blocked unsafe URL while resolving %s: %s", url, exc)
        return last_safe
    except httpx.HTTPError as exc:
        logger.warning("Could not resolve %s: %s", url, exc)
        return last_safe
    return last_safe
