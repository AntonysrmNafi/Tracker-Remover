import linkcleaner.link_processor as link_processor
from linkcleaner.link_processor import format_link_block, format_reply, process_url


def test_format_link_block_lists_removed_trackers():
    block = format_link_block(
        original="https://example.com/a?utm_source=fb",
        cleaned="https://example.com/a",
        removed_params=["utm_source"],
        was_redirected=False,
    )
    assert block == (
        "Your Link : https://example.com/a?utm_source=fb\n"
        "Clean & Secure Link : https://example.com/a\n"
        "Tracker : utm_source"
    )


def test_format_link_block_notes_short_url_resolution():
    block = format_link_block(
        original="https://bit.ly/abc",
        cleaned="https://example.com/real-page",
        removed_params=[],
        was_redirected=True,
    )
    assert "Tracker : Short URL (resolved)" in block


def test_format_link_block_combines_trackers_and_short_url():
    block = format_link_block(
        original="https://bit.ly/abc",
        cleaned="https://example.com/real-page",
        removed_params=["fbclid", "utm_source"],
        was_redirected=True,
    )
    assert "Tracker : fbclid, utm_source, Short URL (resolved)" in block


def test_format_link_block_no_trackers_found():
    block = format_link_block(
        original="https://example.com/a",
        cleaned="https://example.com/a",
        removed_params=[],
        was_redirected=False,
    )
    assert "Tracker : None found" in block


def test_format_link_block_dedupes_repeated_params():
    block = format_link_block(
        original="https://example.com/a?fbclid=x&fbclid=y",
        cleaned="https://example.com/a",
        removed_params=["fbclid", "fbclid"],
        was_redirected=False,
    )
    assert "Tracker : fbclid" in block
    assert block.count("fbclid") == 3  # twice in "Your Link" query string, once in "Tracker"


def test_format_reply_joins_multiple_blocks_with_blank_line():
    results = [
        {
            "original": "https://example.com/a?utm_source=fb",
            "cleaned": "https://example.com/a",
            "removed_params": ["utm_source"],
            "was_redirected": False,
        },
        {
            "original": "https://example.com/b",
            "cleaned": "https://example.com/b",
            "removed_params": [],
            "was_redirected": False,
        },
    ]
    reply = format_reply(results)
    blocks = reply.split("\n\n")
    assert len(blocks) == 2
    assert "Your Link : https://example.com/a?utm_source=fb" in blocks[0]
    assert "Your Link : https://example.com/b" in blocks[1]


def test_format_link_block_reports_unresolvable_short_link():
    block = format_link_block(
        original="https://vm.tiktok.com/ZMjK12345/",
        cleaned="https://vm.tiktok.com/ZMjK12345/",
        removed_params=[],
        was_redirected=False,
        attempted_resolution=True,
    )
    assert "Tracker : Short URL (could not verify destination, kept original)" in block


def test_format_link_block_prefers_resolved_over_attempted_flag():
    # When resolution DID succeed, we should not also claim it failed.
    block = format_link_block(
        original="https://bit.ly/abc",
        cleaned="https://example.com/real-page",
        removed_params=[],
        was_redirected=True,
        attempted_resolution=True,
    )
    assert block.count("Tracker :") == 1
    assert "Tracker : Short URL (resolved)" in block
    assert "could not verify" not in block


async def test_process_url_reports_attempted_resolution_for_shortener(monkeypatch):
    async def _fake_resolve_no_change(url, transport=None):
        return url  # simulates a shortener we couldn't actually resolve

    monkeypatch.setattr(link_processor, "resolve_final_url", _fake_resolve_no_change)

    result = await process_url("https://vm.tiktok.com/ZMjK12345/")
    assert result["attempted_resolution"] is True
    assert result["was_redirected"] is False


async def test_process_url_does_not_flag_attempted_resolution_for_direct_links(monkeypatch):
    async def _fail_if_called(url, transport=None):
        raise AssertionError("resolve_final_url must not be called for a direct link")

    monkeypatch.setattr(link_processor, "resolve_final_url", _fail_if_called)

    result = await process_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert result["attempted_resolution"] is False
    assert result["was_redirected"] is False


async def test_process_url_reports_original_verbatim_and_strips_trackers():
    # "Your Link" must be exactly what the user sent, trailing punctuation
    # from the surrounding sentence and all. The network call inside
    # process_url will fail in this sandbox (no real network access), which
    # exercises the safe-fallback path: it should still clean whatever
    # trackers were already present in the (unresolved) URL.
    raw_url = "https://example.com/a?utm_source=fb)."

    result = await process_url(raw_url)

    assert result["original"] == raw_url
    assert result["cleaned"] == "https://example.com/a"
    assert "utm_source" in result["removed_params"]


async def test_process_url_stays_quiet_for_heuristic_false_positive(monkeypatch):
    """A normal blog-style link with a short-ish slug might trip the generic
    heuristic and get fetched, but if it turns out not to redirect at all it
    must NOT get the scary 'could not verify destination' message, since it
    was never actually a shortener."""

    async def _fake_resolve_no_change(url, transport=None):
        return url  # no redirect happened, it's just a normal page

    monkeypatch.setattr(link_processor, "resolve_final_url", _fake_resolve_no_change)

    result = await process_url("https://totallynewshortener.io/aB3x9")
    assert result["was_redirected"] is False
    assert result["attempted_resolution"] is False


async def test_process_url_still_honest_for_confirmed_shortener(monkeypatch):
    async def _fake_resolve_no_change(url, transport=None):
        return url  # site blocked us / no real redirect available

    monkeypatch.setattr(link_processor, "resolve_final_url", _fake_resolve_no_change)

    result = await process_url("https://shorturl.at/bh0P2")
    assert result["was_redirected"] is False
    assert result["attempted_resolution"] is True


async def test_process_url_never_fetches_a_direct_linkedin_link(monkeypatch):
    """Regression test for the exact bug reported: a full LinkedIn post URL
    was being fetched, LinkedIn's authwall redirected it to the bare
    homepage, and the real path was lost. It must not be fetched at all."""

    async def _fail_if_called(url, transport=None):
        raise AssertionError(f"resolve_final_url must not be called for a direct link: {url}")

    monkeypatch.setattr(link_processor, "resolve_final_url", _fail_if_called)

    raw_url = (
        "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678"
        "?trk=public_post_share"
    )
    result = await process_url(raw_url)

    assert result["cleaned"] == (
        "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678"
    )
    assert result["was_redirected"] is False
    assert "trk" in result["removed_params"]
