"""Which query parameters (and URL fragments) count as trackers, per
platform, and the logic that strips them from a URL."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ---------------------------------------------------------------------------
# Tracking parameters that are stripped regardless of domain
# ---------------------------------------------------------------------------
GENERIC_TRACKING_PARAMS_EXACT = {
    "fbclid", "mibextid", "gclid", "gclsrc", "dclid", "gbraid", "wbraid",
    "gad_source", "msclkid", "ttclid", "twclid", "yclid", "ysclid",
    "igsh", "igshid", "si", "ncid", "cmpid", "icid", "ito",
    "mc_cid", "mc_eid", "mkt_tok", "vero_id", "_hsenc", "_hsmi",
    "hsctatracking", "elqtrackid", "oly_enc_id", "oly_anon_id",
    "ref", "ref_src", "ref_url", "spm", "scm",
}

GENERIC_TRACKING_PARAMS_PREFIX = (
    "utm_", "pf_rd_", "pd_rd_", "__cft__", "__tn__",
)

# ---------------------------------------------------------------------------
# Per-platform extra tracking parameters and whether to drop the fragment.
# Matched against the request host, including subdomains (e.g. m.youtube.com
# matches the "youtube.com" rule).
# ---------------------------------------------------------------------------
PLATFORM_RULES = [
    (
        frozenset({"facebook.com", "fb.com", "fb.watch", "fb.me", "messenger.com", "m.me"}),
        frozenset({
            "fbclid", "mibextid", "__tn__", "refsrc", "source", "extid",
            "paipv", "eav", "notif_id", "notif_t", "ref_component", "actorid", "hrc",
            "rdid", "share_url",
        }),
        True,
    ),
    (
        frozenset({"youtube.com", "youtu.be", "music.youtube.com"}),
        frozenset({"si", "feature", "ab_channel", "pp", "kw"}),
        False,
    ),
    (
        frozenset({"twitter.com", "x.com", "t.co"}),
        frozenset({"s", "t", "src"}),
        False,
    ),
    (
        frozenset({"instagram.com", "instagr.am"}),
        frozenset({"igsh", "igshid"}),
        False,
    ),
    (
        frozenset({"tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}),
        frozenset({
            "share_app_id", "checksum", "sender_device", "sender_web_id",
            "tt_from", "is_from_webapp", "is_copy_url", "u_code",
            "share_item_id", "source", "enter_from", "_t", "_r", "ttclid",
        }),
        True,
    ),
    (
        frozenset({"linkedin.com", "lnkd.in"}),
        frozenset({
            "trk", "trkcampaign", "trkemail", "rcm", "midtoken",
            "midsig", "originalsubdomain", "lipi", "otptoken", "eid",
        }),
        False,
    ),
    (
        frozenset({"snapchat.com", "story.snapchat.com"}),
        frozenset({"share_id", "sc_cid", "attributionid"}),
        False,
    ),
    (
        frozenset({"reddit.com", "redd.it"}),
        frozenset({"share_id"}),
        False,
    ),
    (
        frozenset({"pinterest.com", "pin.it"}),
        frozenset({"sender", "sender_id", "invite_code", "share_id"}),
        False,
    ),
    (
        frozenset({"amazon.com", "amazon.in", "amazon.co.uk", "amazon.de"}),
        frozenset({
            "tag", "ref_", "linkcode", "camp", "creative",
            "creativeasin", "psc", "spla", "keywords_id",
        }),
        False,
    ),
    (
        frozenset({"google.com"}),
        frozenset({"ved", "uact", "sxsrf", "ei", "sa", "gs_lcrp", "g_ep", "g_st"}),
        False,
    ),
    (
        frozenset({"spotify.com", "open.spotify.com", "spotify.link"}),
        frozenset({"si", "nd"}),
        False,
    ),
]


def find_platform_rule(domain: str) -> tuple[frozenset, bool]:
    """Returns (extra_tracking_params, strip_fragment) for a domain, or
    (empty set, False) if no platform rule matches."""
    for domains, params, strip_fragment in PLATFORM_RULES:
        if domain in domains or any(domain.endswith("." + base) for base in domains):
            return params, strip_fragment
    return frozenset(), False


def _clean_url_internal(url: str) -> tuple[str, list[str]]:
    """Returns (cleaned_url, removed_param_names). If the URL can't be
    parsed, returns it unchanged with an empty removed-params list."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url, []

    if not parsed.scheme or not parsed.netloc:
        return url, []

    domain = parsed.netloc.lower().removeprefix("www.")
    domain_extra_params, strip_fragment = find_platform_rule(domain)

    cleaned_params = []
    removed_params: list[str] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if (
            key_lower in GENERIC_TRACKING_PARAMS_EXACT
            or key_lower in domain_extra_params
            or any(key_lower.startswith(prefix) for prefix in GENERIC_TRACKING_PARAMS_PREFIX)
        ):
            removed_params.append(key)
            continue
        cleaned_params.append((key, value))

    if strip_fragment and parsed.fragment:
        removed_params.append("fragment")

    new_query = urlencode(cleaned_params, doseq=True)
    fragment = "" if strip_fragment else parsed.fragment

    cleaned = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, fragment))
    return cleaned, removed_params


def clean_url(url: str) -> str:
    """Strips known tracking parameters (and fragment, where applicable)
    from a URL. Does not resolve redirects; see linkcleaner.link_resolver
    for that."""
    cleaned, _removed_params = _clean_url_internal(url)
    return cleaned


def clean_url_with_trackers(url: str) -> tuple[str, list[str]]:
    """Same as clean_url, but also reports which tracking params (and
    "fragment", if dropped) were removed."""
    return _clean_url_internal(url)
