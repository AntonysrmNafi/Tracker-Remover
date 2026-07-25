import ipaddress

import httpx
import pytest

import linkcleaner.link_resolver as link_resolver
import linkcleaner.ssrf_guard as ssrf_guard
from linkcleaner.tracking_rules import clean_url


def test_headers_for_facebook_host_uses_crawler_ua():
    headers = link_resolver.headers_for("https://www.facebook.com/share/v/1BULkwnpQA/")
    assert headers["User-Agent"] == link_resolver.FACEBOOK_CRAWLER_HEADERS["User-Agent"]


def test_headers_for_facebook_subdomain_uses_crawler_ua():
    headers = link_resolver.headers_for("https://m.facebook.com/reel/123/")
    assert headers["User-Agent"] == link_resolver.FACEBOOK_CRAWLER_HEADERS["User-Agent"]


def test_headers_for_non_facebook_host_uses_default():
    assert link_resolver.headers_for("https://www.youtube.com/watch?v=abc") == {}


def test_headers_for_twitter_host_uses_twitterbot_ua():
    headers = link_resolver.headers_for("https://x.com/user/status/123")
    assert headers["User-Agent"] == link_resolver.TWITTER_CRAWLER_HEADERS["User-Agent"]


def test_headers_for_tco_host_uses_twitterbot_ua():
    headers = link_resolver.headers_for("https://t.co/abc123")
    assert headers["User-Agent"] == link_resolver.TWITTER_CRAWLER_HEADERS["User-Agent"]


def test_guard_keeps_resolved_url_when_it_has_a_real_path():
    original = "https://lnkd.in/abc123"
    resolved = "https://www.linkedin.com/posts/user_activity-123"
    assert link_resolver.guard_against_authwall(original, resolved) == resolved


def test_guard_falls_back_when_resolved_lands_on_bare_homepage():
    original = "https://lnkd.in/abc123"
    resolved = "https://www.linkedin.com/"
    assert link_resolver.guard_against_authwall(original, resolved) == original


def test_guard_falls_back_on_authwall_path():
    original = "https://lnkd.in/abc123"
    resolved = "https://www.linkedin.com/authwall?trk=abc&sessionRedirect=..."
    assert link_resolver.guard_against_authwall(original, resolved) == original


def test_guard_falls_back_on_login_path():
    original = "https://lnkd.in/abc123"
    resolved = "https://www.linkedin.com/login"
    assert link_resolver.guard_against_authwall(original, resolved) == original


def test_guard_falls_back_on_fbwatch_login_redirect():
    # A private/restricted fb.watch video bounces to a login page instead of
    # the real content; the guard must keep the original short link.
    original = "https://fb.watch/n1a2b3c4/"
    resolved = "https://www.facebook.com/login.php?next=https%3A%2F%2Fwww.facebook.com%2Fwatch%2F%3Fv%3D123"
    assert link_resolver.guard_against_authwall(original, resolved) == original


def test_guard_allows_bare_homepage_when_original_had_no_path_either():
    original = "https://lnkd.in/"
    resolved = "https://www.linkedin.com/"
    assert link_resolver.guard_against_authwall(original, resolved) == resolved


def test_guard_accepts_bare_homepage_on_a_non_bounce_prone_domain():
    # Regression test for the exact reported bug: shorturl.at/bh0P2 legitimately
    # resolves to a bare homepage (https://drive.proton.me/), which is not
    # LinkedIn/TikTok, so it must be accepted rather than discarded.
    original = "https://shorturl.at/bh0P2"
    resolved = "https://drive.proton.me/"
    assert link_resolver.guard_against_authwall(original, resolved) == resolved


def test_guard_still_blocks_bare_homepage_on_tiktok():
    original = "https://vm.tiktok.com/ZMjK12345/"
    resolved = "https://www.tiktok.com/?_r=1"
    assert link_resolver.guard_against_authwall(original, resolved) == original


@pytest.fixture
def bypass_dns(monkeypatch):
    """Named hosts can't be resolved from this test sandbox, so pretend any
    named host is public. IP-literal hosts still go through the real
    blocking logic, so tests that redirect to a private/metadata IP are
    still meaningful."""

    async def _fake_assert_host_is_public(host):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return  # named host, pretend it resolved to a public address
        if ssrf_guard.is_blocked_ip(host):
            raise ssrf_guard.UnsafeURLError(f"blocked internal/private address: {host}")

    monkeypatch.setattr(ssrf_guard, "assert_host_is_public", _fake_assert_host_is_public)


async def test_facebook_share_link_resolved_via_og_url(bypass_dns):
    """Reproduces the exact case reported: a facebook.com/share/v/... link
    that doesn't send an HTTP redirect but embeds the real reel URL in an
    og:url meta tag."""
    share_url = "https://www.facebook.com/share/v/1BULkwnpQA/"
    reel_url = "https://www.facebook.com/reel/2180228049484735/"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == share_url:
            html = f'<html><head><meta property="og:url" content="{reel_url}" /></head></html>'
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
        if str(request.url) == reel_url:
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")
        raise AssertionError(f"unexpected request to {request.url}")

    result = await link_resolver.resolve_final_url(share_url, transport=httpx.MockTransport(handler))
    assert result == reel_url


async def test_normal_redirect_chain(bypass_dns):
    short_url = "https://short.example/abc"
    final_url = "https://long.example/real-article?utm_source=x"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == short_url:
            return httpx.Response(302, headers={"location": final_url})
        if str(request.url) == final_url:
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")
        raise AssertionError(f"unexpected request to {request.url}")

    result = await link_resolver.resolve_final_url(short_url, transport=httpx.MockTransport(handler))
    assert result == final_url


async def test_shorturl_at_resolves_and_cleans(bypass_dns):
    """End-to-end reproduction of the reported case: shorturl.at wasn't
    being resolved or cleaned at all."""
    short_url = "https://shorturl.at/bh0P2"
    final_url = "https://example.com/real-article?utm_source=share&id=42"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == short_url:
            return httpx.Response(302, headers={"location": final_url})
        if str(request.url) == final_url:
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")
        raise AssertionError(f"unexpected request to {request.url}")

    resolved = await link_resolver.resolve_final_url(short_url, transport=httpx.MockTransport(handler))
    assert resolved == final_url
    cleaned = clean_url(resolved)
    assert cleaned == "https://example.com/real-article?id=42"


async def test_shorturl_at_resolving_to_a_homepage_is_accepted(bypass_dns):
    """Exact production case from the log: shorturl.at/bh0P2 legitimately
    redirects to a bare homepage. Must be accepted, not discarded."""
    short_url = "https://shorturl.at/bh0P2"
    homepage_url = "https://drive.proton.me/"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == short_url:
            return httpx.Response(302, headers={"location": homepage_url})
        if str(request.url) == homepage_url:
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")
        raise AssertionError(f"unexpected request to {request.url}")

    resolved = await link_resolver.resolve_final_url(short_url, transport=httpx.MockTransport(handler))
    assert resolved == homepage_url


async def test_redirect_to_private_metadata_ip_is_blocked(bypass_dns):
    public_url = "https://short.example/abc"
    unsafe_target = "http://169.254.169.254/latest/meta-data/"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == public_url:
            return httpx.Response(302, headers={"location": unsafe_target})
        raise AssertionError("the unsafe target must never actually be fetched")

    result = await link_resolver.resolve_final_url(public_url, transport=httpx.MockTransport(handler))
    assert result == public_url


async def test_shortlink_hitting_authwall_falls_back_to_short_url(bypass_dns):
    short_url = "https://lnkd.in/abc123"
    authwall_url = "https://www.linkedin.com/authwall?trk=x&sessionRedirect=y"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == short_url:
            return httpx.Response(302, headers={"location": authwall_url})
        if str(request.url) == authwall_url:
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text="<html><body>Sign in</body></html>"
            )
        raise AssertionError(f"unexpected request to {request.url}")

    result = await link_resolver.resolve_final_url(short_url, transport=httpx.MockTransport(handler))
    assert result == short_url


async def test_fbwatch_private_video_login_redirect_falls_back(bypass_dns):
    """Exact reported scenario: a private/restricted fb.watch video bounces
    to a login page instead of the real content. The bot must keep the
    original short link rather than 'clean' the login URL into a link."""
    short_url = "https://fb.watch/n1a2b3c4/"
    login_url = "https://www.facebook.com/login.php?next=https%3A%2F%2Fwww.facebook.com%2Fwatch%2F%3Fv%3D123"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == short_url:
            return httpx.Response(302, headers={"location": login_url})
        if str(request.url) == login_url:
            return httpx.Response(
                200, headers={"content-type": "text/html"}, text="<html><body>Log in</body></html>"
            )
        raise AssertionError(f"unexpected request to {request.url}")

    result = await link_resolver.resolve_final_url(short_url, transport=httpx.MockTransport(handler))
    assert result == short_url



    """Matches a real production log: vm.tiktok.com bounced to the bare
    tiktok.com homepage (a JS-based click-tracking bounce our plain HTTP
    client can't follow) instead of the actual video."""
    short_url = "https://vm.tiktok.com/ZMjK12345/"
    homepage_url = "https://www.tiktok.com/?_r=1"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == short_url:
            return httpx.Response(302, headers={"location": homepage_url})
        if str(request.url) == homepage_url:
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")
        raise AssertionError(f"unexpected request to {request.url}")

    result = await link_resolver.resolve_final_url(short_url, transport=httpx.MockTransport(handler))
    assert result == short_url


async def test_end_to_end_resolve_and_clean_facebook_share_link(bypass_dns):
    share_url = "https://www.facebook.com/share/v/1BULkwnpQA/"
    reel_url_with_trackers = (
        "https://www.facebook.com/reel/2180228049484735/?rdid=iSFKxxvza9rKekAD"
        "&share_url=https%3A%2F%2Fwww.facebook.com%2Fshare%2Fv%2F1BULkwnpQA%2F"
        "&fbclid=xyz"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == share_url:
            html = (
                '<html><head><meta property="og:url" '
                f'content="{reel_url_with_trackers}" /></head></html>'
            )
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")

    transport = httpx.MockTransport(handler)
    resolved = await link_resolver.resolve_final_url(share_url, transport=transport)
    cleaned = clean_url(resolved)
    assert cleaned == "https://www.facebook.com/reel/2180228049484735/"
