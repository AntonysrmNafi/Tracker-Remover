# Link Cleaner Bot

A Telegram bot that cleans up messy social media links. Send it any share link and it sends back a clean, tracker-free version — plus tells you exactly what it removed.

## What it does

- **Removes tracking parameters** — the ugly `?utm_source=...`, `?fbclid=...`, `?igshid=...` junk that gets tacked onto links when you share them. These trackers let platforms and advertisers follow you around and know exactly who shared what to whom.
- **Resolves shortened links** — if you paste a short link (`bit.ly/...`, `vm.tiktok.com/...`, a Facebook `/share/...` link, etc.), the bot follows it to the real destination first, then cleans that.
- **Works with multiple links at once** — paste several links in one message, get a clean result for each.
- **Private chat only** — the bot doesn't respond in group chats, so it can't be added to a group and used on other people's messages.

## What you get back

For every link you send:

```
Your Link : <exactly what you sent>
Clean & Secure Link : <the clean, tracker-free link>
Tracker : <what was removed, e.g. "fbclid, utm_source" or "Short URL (resolved)" or "None found">
```

## Supported platforms

| Platform | What gets cleaned |
|---|---|
| **Facebook / Messenger** | `fbclid`, share-tracking IDs, `/share/v/` and `/share/r/` links resolved to the real post |
| **Instagram** | `igsh`, `igshid` share IDs |
| **YouTube** | `si` tracking ID (keeps `v`, `t`, `list` — the parts that actually matter) |
| **X / Twitter** | `s`, `t` tracking params, `t.co` short links resolved |
| **TikTok** | share/tracking IDs, `vm.tiktok.com` / `vt.tiktok.com` short links resolved |
| **LinkedIn** | `trk` and related tracking params, `lnkd.in` short links resolved |
| **Snapchat** | share/attribution IDs |
| **Reddit** | `share_id`, `redd.it` short links resolved |
| **Pinterest** | sender/invite tracking IDs, `pin.it` short links resolved |
| **Amazon** | affiliate tags and referral tracking |
| **Google Search / Maps** | search-session tracking params |
| **Spotify** | `si` share ID |
| **Anything else** | generic trackers (`utm_*`, `gclid`, `msclkid`, `ttclid`, `ysclid`, and more) are stripped from any link, and 45+ known URL shorteners (`tinyurl.com`, `is.gd`, `rebrand.ly`, `shorturl.at`, and more) are resolved — plus unfamiliar shorteners are usually detected automatically |

## How to use it

1. Open a private chat with the bot.
2. Send `/start` for a quick intro.
3. Paste any link (or several) — the bot replies automatically, no command needed.

## A note on limits

- A few shorteners (notably some TikTok short links) use a bounce mechanism that a plain link-following bot can't fully follow. In that case the bot is honest about it — "could not verify destination, kept original" — instead of guessing.
- The bot never guesses a "cleaned" link into something worse: if resolving a short link would land on a login/authwall page, it keeps your original link instead.

---

## For developers

<details>
<summary>Project structure, local setup, and deployment</summary>

The bot is a Python package, `linkcleaner/`, split by responsibility:

```
linkcleaner/
├── __init__.py
├── __main__.py            entrypoint: `python -m linkcleaner`
├── url_utils.py            URL regex + trailing-punctuation stripping
├── tracking_rules.py       which query params/fragments are trackers, per platform, and clean_url()
├── shortener_detection.py  decides whether a link needs to be fetched to find its real destination
├── ssrf_guard.py           validates a host is safe to fetch (SSRF protection)
├── link_resolver.py        follows redirects, crawler headers, HTML fallback extraction, authwall guard
├── link_processor.py       ties resolution + cleaning together, builds the reply text
├── rate_limiter.py         per-user sliding-window rate limit
└── telegram_bot.py         the only module that talks to python-telegram-bot
```

```
tests/
├── conftest.py
├── test_tracking_rules.py
├── test_shortener_detection.py
├── test_ssrf_guard.py
├── test_link_resolver.py
├── test_canonical_extraction.py
└── test_link_processor.py
```

### Production hardening

- **SSRF protection**: every redirect hop's hostname is resolved and checked before it's fetched. Private/loopback/link-local/reserved/multicast IPs (including cloud metadata endpoints like `169.254.169.254`) are blocked. Redirects are followed manually, one hop at a time, so every hop is re-validated.
- **No large downloads**: responses are streamed and never fully read — only headers/status/redirect target (and a capped chunk of HTML when needed) are inspected.
- **Authwall guard**: a resolved URL that looks like a login/authwall bounce is discarded in favor of the original link.
- **Private chats only**, **per-user rate limiting**, **Telegram flood-control handling**, **global error handler**, **non-root Docker container**, **async HTTP via `httpx`**.

### Run locally

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and set `BOT_TOKEN=your_token`.
4. `python -m linkcleaner`

### Deploy on Railway

1. Push this repo to GitHub.
2. In Railway: New Project → Deploy from GitHub repo.
3. Add the `BOT_TOKEN` variable in the Railway project's Variables tab.
4. Railway builds/runs the `Dockerfile` automatically.
5. Make sure the service has **1 replica** — two instances polling the same `BOT_TOKEN` causes a harmless-but-noisy `Conflict` error during redeploys.

### Tests and lint

```
pip install -r requirements-dev.txt
ruff check .
pytest -v
```

GitHub Actions (`.github/workflows/ci.yml`) runs both on every push and pull request.

### Extending it

- Add tracking params in `linkcleaner/tracking_rules.py` (`GENERIC_TRACKING_PARAMS_EXACT`, `GENERIC_TRACKING_PARAMS_PREFIX`, or a new/updated entry in `PLATFORM_RULES`).
- Add shorteners in `linkcleaner/shortener_detection.py` (`SHORTENER_DOMAINS`), though the heuristic in `looks_like_unknown_shortlink` catches most new ones automatically.
- Add a matching test case alongside whichever module you change.

### Known limitations

- The SSRF guard doesn't defend against DNS-rebinding attacks that swap the IP between the check and the actual connect — an accepted tradeoff for this bot's threat model.
- `vm.tiktok.com`-style JS-based click-tracking bounces can't be followed by a plain HTTP client; the bot falls back to the original link rather than showing a broken result.

</details>
