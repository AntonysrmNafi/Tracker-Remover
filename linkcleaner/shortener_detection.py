"""Decides whether a link needs to be fetched over the network to find its
real destination (a known shortener, a Facebook /share/ wrapper, or
something that merely looks like an unfamiliar shortener), or whether it's
already a direct link that can be cleaned in place.

Only resolving when actually necessary matters for more than performance: a
full/direct link (e.g. a normal linkedin.com post URL) is already the real
destination, and fetching it can hit a platform's login/authwall page
(LinkedIn does this for logged-out requests), redirecting to a generic
homepage and destroying the real path for no reason.
"""

import re
from urllib.parse import urlsplit

from linkcleaner.tracking_rules import PLATFORM_RULES

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


def looks_like_unknown_shortlink(url: str, domain: str) -> bool:
    """True if `domain` isn't one we already recognize, and `url`'s path is
    a single short random-looking segment with no query string — the
    typical shape of a URL-shortener slug (e.g. "/bh0P2")."""
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


def is_known_shortener(domain: str, path: str) -> bool:
    """True for a domain in our curated shortener list, or a Facebook
    /share/v/... or /share/r/... wrapper path."""
    if domain in SHORTENER_DOMAINS or any(domain.endswith("." + d) for d in SHORTENER_DOMAINS):
        return True
    # Facebook's /share/v/... and /share/r/... paths are wrapper links even
    # though they live on facebook.com itself; a direct facebook.com/reel/...
    # or facebook.com/watch?v=... link does not need resolving.
    if domain == "facebook.com" or domain.endswith(".facebook.com"):
        return path.startswith("/share/")
    return False


def needs_resolution(url: str) -> bool:
    """True if `url` should be fetched over the network to find its real
    destination before cleaning it."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False

    domain = parsed.netloc.lower().removeprefix("www.")
    if is_known_shortener(domain, parsed.path):
        return True

    return looks_like_unknown_shortlink(url, domain)
