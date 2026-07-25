"""Small, dependency-free URL string helpers shared by the other modules."""

import re

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
