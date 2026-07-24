import pytest

import main


@pytest.mark.parametrize(
    "url",
    [
        "https://bit.ly/abc123",
        "https://t.co/abc123",
        "https://lnkd.in/abc123",
        "https://vm.tiktok.com/abc123/",
        "https://vt.tiktok.com/abc123/",
        "https://fb.watch/abc123/",
        "https://fb.me/abc123",
        "https://goo.gl/abc123",
        "https://maps.app.goo.gl/abc123",
        "https://amzn.to/abc123",
        "https://pin.it/abc123",
        "https://redd.it/abc123",
        "https://spotify.link/abc123",
        "https://www.facebook.com/share/v/1BULkwnpQA/",
        "https://www.facebook.com/share/r/1BULkwnpQA/",
    ],
)
def test_needs_resolution_true_for_shorteners_and_facebook_share(url):
    assert main.needs_resolution(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678?trk=public_post_share",
        "https://www.linkedin.com/posts/user_activity-123",
        "https://www.facebook.com/reel/2180228049484735/",
        "https://www.facebook.com/watch?v=123456",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.instagram.com/p/ABC123/",
        "https://x.com/user/status/12345",
        "https://www.tiktok.com/@user/video/123",
        "https://www.amazon.com/dp/B08XYZ",
        "https://www.reddit.com/r/test/comments/abc/title/",
        "https://open.spotify.com/track/abc123",
    ],
)
def test_needs_resolution_false_for_direct_links(url):
    assert main.needs_resolution(url) is False


async def test_process_url_never_fetches_a_direct_linkedin_link(monkeypatch):
    """Regression test for the exact bug reported: a full LinkedIn post URL
    was being fetched, LinkedIn's authwall redirected it to the bare
    homepage, and the real path was lost. It must not be fetched at all."""

    async def _fail_if_called(url, transport=None):
        raise AssertionError(f"resolve_final_url must not be called for a direct link: {url}")

    monkeypatch.setattr(main, "resolve_final_url", _fail_if_called)

    raw_url = (
        "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678"
        "?trk=public_post_share"
    )
    result = await main.process_url(raw_url)

    assert result["cleaned"] == (
        "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678"
    )
    assert result["was_redirected"] is False
    assert "trk" in result["removed_params"]
