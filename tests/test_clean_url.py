from main import clean_url


def test_strips_generic_utm_params():
    url = "https://example.com/article?utm_source=fb&utm_medium=social&id=42"
    assert clean_url(url) == "https://example.com/article?id=42"


def test_strips_fbclid_on_any_domain():
    url = "https://example.com/page?fbclid=abc123&ref=home"
    assert clean_url(url) == "https://example.com/page"


def test_youtube_strips_si_but_keeps_v_and_t():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=xyz&t=30"
    assert clean_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30"


def test_youtube_short_domain_strips_si():
    url = "https://youtu.be/dQw4w9WgXcQ?si=xyz"
    assert clean_url(url) == "https://youtu.be/dQw4w9WgXcQ"


def test_instagram_strips_igsh():
    url = "https://www.instagram.com/p/ABC123/?igsh=xyz&utm_source=ig_web_copy_link"
    assert clean_url(url) == "https://www.instagram.com/p/ABC123/"


def test_facebook_strips_fbclid_and_fragment():
    url = "https://www.facebook.com/story.php?story_fbid=1&id=2&fbclid=abc#footer"
    assert clean_url(url) == "https://www.facebook.com/story.php?story_fbid=1&id=2"


def test_facebook_strips_rdid_and_share_url():
    url = (
        "https://www.facebook.com/reel/2180228049484735/"
        "?rdid=iSFKxxvza9rKekAD"
        "&share_url=https%3A%2F%2Fwww.facebook.com%2Fshare%2Fv%2F1BULkwnpQA%2F"
        "&fbclid=xyz"
    )
    assert clean_url(url) == "https://www.facebook.com/reel/2180228049484735/"


def test_twitter_strips_s_and_t():
    url = "https://x.com/user/status/12345?s=20&t=abc123"
    assert clean_url(url) == "https://x.com/user/status/12345"


def test_twitter_params_not_stripped_on_unrelated_domain():
    # "s" and "t" are only trackers on twitter/x, not generically
    url = "https://example.com/page?s=20&t=abc123"
    assert clean_url(url) == "https://example.com/page?s=20&t=abc123"


def test_tiktok_strips_share_params():
    url = "https://www.tiktok.com/@user/video/123?is_from_webapp=1&sender_device=pc&_r=1"
    assert clean_url(url) == "https://www.tiktok.com/@user/video/123"


def test_amazon_strips_affiliate_tag():
    url = "https://www.amazon.com/dp/B08XYZ/ref=abc?tag=partner-20&psc=1"
    assert clean_url(url) == "https://www.amazon.com/dp/B08XYZ/ref=abc"


def test_reddit_strips_share_id_keeps_context():
    url = "https://www.reddit.com/r/test/comments/abc/title/?share_id=xyz&context=3"
    assert clean_url(url) == "https://www.reddit.com/r/test/comments/abc/title/?context=3"


def test_linkedin_strips_trk():
    url = "https://www.linkedin.com/posts/user_activity-123?trk=feed_main&trkEmail=xyz"
    assert clean_url(url) == "https://www.linkedin.com/posts/user_activity-123"


def test_google_search_strips_tracking_state():
    url = "https://www.google.com/search?q=hello&ved=abc&uact=8&sxsrf=xyz"
    assert clean_url(url) == "https://www.google.com/search?q=hello"


def test_spotify_strips_si():
    url = "https://open.spotify.com/track/abc123?si=xyz"
    assert clean_url(url) == "https://open.spotify.com/track/abc123"


def test_url_with_no_query_is_unchanged():
    url = "https://example.com/just/a/path"
    assert clean_url(url) == url


def test_invalid_url_returned_as_is():
    assert clean_url("not a url") == "not a url"
