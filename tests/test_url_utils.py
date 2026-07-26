import pytest

from linkcleaner.url_utils import get_domain, strip_trailing_punctuation


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
