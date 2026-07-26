"""Small, dependency-free URL string helpers shared by the other modules."""

import re
from urllib.parse import urlsplit

URL_REGEX = re.compile(r"https?://[^\s]+")
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


def get_domain(url: str) -> str:
    """Returns the lowercased, "www."-stripped host of a URL, or "" if it
    can't be parsed. Used for domain-popularity stats."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return parsed.netloc.lower().removeprefix("www.")
