import ipaddress

import httpx
import pytest

import main


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
        if main._is_blocked_ip(host):
            raise main.UnsafeURLError(f"blocked internal/private address: {host}")

    monkeypatch.setattr(main, "_assert_host_is_public", _fake_assert_host_is_public)


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

    result = await main.resolve_final_url(share_url, transport=httpx.MockTransport(handler))
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

    result = await main.resolve_final_url(short_url, transport=httpx.MockTransport(handler))
    assert result == final_url


async def test_redirect_to_private_metadata_ip_is_blocked(bypass_dns):
    public_url = "https://short.example/abc"
    unsafe_target = "http://169.254.169.254/latest/meta-data/"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == public_url:
            return httpx.Response(302, headers={"location": unsafe_target})
        raise AssertionError("the unsafe target must never actually be fetched")

    result = await main.resolve_final_url(public_url, transport=httpx.MockTransport(handler))
    assert result == public_url


async def test_end_to_end_process_url_cleans_after_resolving(bypass_dns):
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
    resolved = await main.resolve_final_url(share_url, transport=transport)
    cleaned = main.clean_url(resolved)
    assert cleaned == "https://www.facebook.com/reel/2180228049484735/"
