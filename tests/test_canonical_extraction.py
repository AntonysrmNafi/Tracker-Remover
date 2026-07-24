from main import CANONICAL_LINK_RE, OG_URL_RE


def test_og_url_regex_finds_content_after_property():
    html = '<meta property="og:url" content="https://www.facebook.com/reel/123/" />'
    match = OG_URL_RE.search(html)
    assert match is not None
    assert match.group(1) == "https://www.facebook.com/reel/123/"


def test_og_url_regex_finds_property_after_content():
    html = '<meta content="https://www.facebook.com/reel/123/" property="og:url" />'
    match = OG_URL_RE.search(html)
    assert match is not None
    assert match.group(1) == "https://www.facebook.com/reel/123/"


def test_og_url_regex_tolerates_whitespace_around_equals():
    html = '<meta property = "og:url" content = "https://www.facebook.com/reel/123/" />'
    match = OG_URL_RE.search(html)
    assert match is not None
    assert match.group(1) == "https://www.facebook.com/reel/123/"


def test_canonical_link_regex():
    html = '<link rel="canonical" href="https://example.com/real-article" />'
    match = CANONICAL_LINK_RE.search(html)
    assert match is not None
    assert match.group(1) == "https://example.com/real-article"


def test_og_url_regex_ignores_unrelated_meta_tags():
    html = '<meta property="og:title" content="Some Video" /><meta name="description" content="x" />'
    assert OG_URL_RE.search(html) is None


def test_og_url_regex_in_realistic_facebook_head():
    html = """
    <html><head>
    <meta property="og:type" content="video.other" />
    <meta property="og:url" content="https://www.facebook.com/reel/2180228049484735/" />
    <meta property="og:title" content="Reel" />
    </head></html>
    """
    match = OG_URL_RE.search(html)
    assert match is not None
    assert match.group(1) == "https://www.facebook.com/reel/2180228049484735/"
