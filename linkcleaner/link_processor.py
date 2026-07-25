"""Ties shortener detection, resolution, and tracker cleaning together into
the per-link result the bot reports back to the user, and formats that
result into the reply text."""

from urllib.parse import urlsplit

from linkcleaner.link_resolver import resolve_final_url
from linkcleaner.shortener_detection import is_known_shortener, looks_like_unknown_shortlink
from linkcleaner.tracking_rules import clean_url_with_trackers
from linkcleaner.url_utils import strip_trailing_punctuation


async def process_url(raw_url: str) -> dict:
    """Resolve + clean one URL and report what was done to it.

    Returns a dict with:
      original             the exact text the user sent (unmodified)
      cleaned               the resolved, tracker-free URL
      removed_params        tracking query params (and "fragment" if dropped)
      was_redirected        True if the link was a short/redirect link that
                             was successfully followed to a different URL
      attempted_resolution  True if this was a *confirmed* shortener (known
                             domain, or Facebook's /share/ wrapper) we tried
                             to resolve, whether or not it succeeded. A link
                             that only matched the generic short-path
                             heuristic and turned out to be a normal direct
                             link does not set this, so it doesn't get an
                             unnecessary "could not verify" message.
    """
    stripped = strip_trailing_punctuation(raw_url)
    parsed = urlsplit(stripped)
    domain = parsed.netloc.lower().removeprefix("www.")
    confirmed_shortener = is_known_shortener(domain, parsed.path)

    if confirmed_shortener or looks_like_unknown_shortlink(stripped, domain):
        resolved = await resolve_final_url(stripped)
    else:
        resolved = stripped

    cleaned, removed_params = clean_url_with_trackers(resolved)
    return {
        "original": raw_url,
        "cleaned": cleaned,
        "removed_params": removed_params,
        "was_redirected": resolved != stripped,
        "attempted_resolution": confirmed_shortener,
    }


def format_link_block(
    original: str,
    cleaned: str,
    removed_params: list[str],
    was_redirected: bool,
    attempted_resolution: bool = False,
) -> str:
    items = list(dict.fromkeys(removed_params))  # dedupe, keep first-seen order
    if was_redirected:
        items.append("Short URL (resolved)")
    elif attempted_resolution:
        items.append("Short URL (could not verify destination, kept original)")
    tracker_text = ", ".join(items) if items else "None found"

    return (
        f"Your Link : {original}\n"
        f"Clean & Secure Link : {cleaned}\n"
        f"Tracker : {tracker_text}"
    )


def format_reply(results: list[dict]) -> str:
    return "\n\n".join(format_link_block(**result) for result in results)
