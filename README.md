# Link Cleaner Bot

Telegram bot that removes tracking parameters from social media share links. If the link is shortened (bit.ly, vm.tiktok.com, fb.watch, amzn.to, etc.), it follows the redirect first, then cleans the final URL.

**Private chats only.** The bot does not respond in groups at all (by design, see below).

**Supported platforms:** Facebook, Messenger, YouTube, X/Twitter, Instagram, TikTok, LinkedIn, Snapchat, Reddit, Pinterest, Amazon, Google Search/Maps, Spotify, plus generic `utm_*` and other common ad-click trackers (`gclid`, `fbclid`, `msclkid`, `ttclid`, `ysclid`, ...) on any other domain.

## How it works

1. User sends a message containing one or more links, in a private chat.
2. Bot follows redirects (one hop at a time, SSRF-validated at every hop) to resolve the final destination URL.
3. Bot strips known tracking query parameters, using both a generic list and platform-specific rules, and drops the tracking fragment on domains that use it (Facebook, TikTok).
4. Bot replies with the clean link(s).

## Production hardening in this version

- **SSRF protection**: every hop's hostname is resolved and checked before it's fetched. Private/loopback/link-local/reserved/multicast IPs (`127.0.0.1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.169.254` cloud metadata, etc., including IPv6 equivalents) are blocked, so a malicious shortened link can't be used to probe your internal network or cloud metadata endpoint. Redirects are followed manually instead of automatically so each hop gets re-validated.
- **No large downloads**: responses are streamed and the body is never read, only headers/status/redirect target are inspected, then the connection is closed.
- **Private chats only**: `filters.ChatType.PRIVATE` on every handler, so the bot silently ignores anything in groups/supergroups/channels.
- **Per-user rate limiting**: max 8 link-cleaning requests per 60 seconds per user (in-memory sliding window) to stop spam.
- **Telegram flood control**: `AIORateLimiter` automatically paces and retries outgoing Telegram API calls.
- **Global error handler**: unexpected exceptions are logged and reported to the user instead of crashing the bot.
- **Non-root Docker container**: the app runs as an unprivileged `app` user, not root.
- **Async HTTP** via `httpx` instead of blocking `requests` + threads.

## Run locally

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and paste your token:
   ```
   BOT_TOKEN=your_bot_token_from_botfather
   ```
   `main.py` loads `.env` automatically via `python-dotenv`.
4. Run:
   ```
   python main.py
   ```

## Deploy on Railway

1. Push this folder to a GitHub repository (`.env` is git-ignored, it never gets committed).
2. In Railway, create a new project and choose "Deploy from GitHub repo", select this repo.
3. In the Railway project's Variables tab, add:
   - `BOT_TOKEN` = your token from BotFather
4. Railway detects the `Dockerfile` and builds/runs the container automatically. No start command needed, it's set by `CMD` in the Dockerfile.
5. Deploy. Check the Deployments logs for "Bot starting..." to confirm it's running.

Note: this bot uses long polling (`run_polling`), not webhooks, so no public URL or port is needed on Railway.

## Running tests and lint locally

```
pip install -r requirements-dev.txt
ruff check .
pytest -v
```

`tests/test_clean_url.py` covers the per-platform cleaning rules, `tests/test_ssrf.py` covers the private/internal IP blocking. GitHub Actions (`.github/workflows/ci.yml`) runs both automatically on every push and pull request.

## Adding more tracking parameters

Open `main.py` and edit:
- `GENERIC_TRACKING_PARAMS_EXACT` for exact parameter names stripped on every domain.
- `GENERIC_TRACKING_PARAMS_PREFIX` for parameter name prefixes (like `utm_`).
- `PLATFORM_RULES` to add/adjust a platform: its domains, its extra tracking parameters, and whether its fragment (`#...`) should be dropped.

Add a matching test case in `tests/test_clean_url.py` when you do.

## Known limitations

- The SSRF guard blocks based on DNS resolution at request time; it does not defend against a sophisticated DNS-rebinding attack that changes the IP between the check and the actual TCP connect. For this bot's threat model (public link-cleaning, not a high-security proxy) that's an accepted tradeoff.
- Google Maps short links (`maps.app.goo.gl`) resolve like any other shortener, but very long Maps URLs may still carry some non-tracking state parameters that are intentionally left untouched to avoid breaking the link.
