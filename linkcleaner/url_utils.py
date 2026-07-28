"""Small, dependency-free URL string helpers shared by the other modules."""

import re
from urllib.parse import urlsplit

# Matches either:
#   1. an explicit http(s):// URL — kept maximally permissive (anything up
#      to whitespace) so IP-based/unusual URLs with an explicit scheme still
#      work, exactly like before
#   2. a schemeless "domain-like" string (www.example.com, example.com/path)
#      — stricter, requires a real-looking dotted hostname ending in an
#      alphabetic TLD, and isn't glued to a preceding word character or "@"
#      (so it doesn't match the domain half of an email address, decimals
#      like "3.5.1", or abbreviations like "e.g.")
URL_REGEX = re.compile(
    r"""
    https?://\S+
    |
    (?<![\w@])
    (?:www\.)?
    (?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+
    [a-zA-Z]{2,24}
    (?:/\S*)?
    """,
    re.VERBOSE,
)

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
TRAILING_CHARS = ".,!?:;'\">"


def strip_trailing_punctuation(url: str) -> str:
    """Drop punctuation a link picked up from the surrounding sentence, e.g.
    "check this out (https://example.com/x)." -> "https://example.com/x".
    Leaves a legitimate closing paren alone, e.g. a Wikipedia
    "...(disambiguation)" link."""
    while url and url[-1] in TRAILING_CHARS:
        url = url[:-1]
    if url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    return url


def ensure_scheme(url: str) -> str:
    """Adds "https://" to a schemeless link (e.g. "www.example.com" or
    "example.com/path") so downstream parsing (urlsplit, resolving,
    cleaning) works the same way it does for links that already have one."""
    if _SCHEME_RE.match(url):
        return url
    return "https://" + url


def get_domain(url: str) -> str:
    """Returns the lowercased, "www."-stripped host of a URL, or "" if it
    can't be parsed. Used for domain-popularity stats."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return parsed.netloc.lower().removeprefix("www.")
