import pytest

from linkcleaner.url_utils import URL_REGEX, ensure_scheme, get_domain, strip_trailing_punctuation


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=abc", "youtube.com"),
        ("https://youtube.com/watch?v=abc", "youtube.com"),
        ("https://open.spotify.com/track/abc", "open.spotify.com"),
        ("https://X.COM/user/status/123", "x.com"),
    ],
)
def test_get_domain_strips_www_and_lowercases(url, expected):
    assert get_domain(url) == expected


def test_get_domain_invalid_url_returns_empty_string():
    assert get_domain("not a url") == ""


def test_strip_trailing_punctuation_removes_sentence_punctuation():
    assert strip_trailing_punctuation("https://example.com/x).") == "https://example.com/x"


def test_strip_trailing_punctuation_keeps_legitimate_closing_paren():
    url = "https://en.wikipedia.org/wiki/Mercury_(planet)"
    assert strip_trailing_punctuation(url) == url


# ---------------------------------------------------------------------------
# URL_REGEX: schemeless link detection (Point 3 bug report)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected_match"),
    [
        ("check https://www.facebook.com/x out", "https://www.facebook.com/x"),
        ("go to www.facebook.com now", "www.facebook.com"),
        ("youtube.com/sgdydy is cool", "youtube.com/sgdydy"),
        ("try https://facebook.com today", "https://facebook.com"),
        ("bbc.co.uk is a news site", "bbc.co.uk"),
        ("visit http://10.0.0.1/path please", "http://10.0.0.1/path"),
    ],
)
def test_url_regex_detects_schemed_and_schemeless_links(text, expected_match):
    assert URL_REGEX.findall(text) == [expected_match]


@pytest.mark.parametrize(
    "text",
    [
        "contact info@example.com please",
        "version 3.5.1 released",
        "e.g. this is a test",
        "Mr. Smith went home",
        "connect to 10.0.0.1 now",  # bare IP, no scheme — not a domain-like TLD match
        "just a plain sentence",
    ],
)
def test_url_regex_avoids_false_positives(text):
    assert URL_REGEX.findall(text) == []


def test_ensure_scheme_adds_https_to_schemeless_link():
    assert ensure_scheme("www.facebook.com") == "https://www.facebook.com"
    assert ensure_scheme("youtube.com/sgdydy") == "https://youtube.com/sgdydy"


def test_ensure_scheme_leaves_schemed_link_unchanged():
    assert ensure_scheme("https://example.com") == "https://example.com"
    assert ensure_scheme("http://example.com") == "http://example.com"

