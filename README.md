# Link Cleaner Bot

Telegram bot that removes tracking parameters (utm_*, fbclid, gclid, igshid, si, etc.) from social media share links. If the link is shortened (bit.ly, vm.tiktok.com, fb.watch, amzn.to, etc.), it follows the redirect first, then cleans the final URL.

## How it works

1. User sends a message containing one or more links.
2. Bot follows redirects to resolve the final destination URL.
3. Bot strips known tracking query parameters and, for a few domains (Facebook, TikTok), the tracking fragment.
4. Bot replies with the clean link(s).

## Run locally

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and paste your token, then export it (or use a tool like `python-dotenv` / your shell):
   ```
   export BOT_TOKEN=your_bot_token_from_botfather
   ```
4. Run:
   ```
   python main.py
   ```

## Deploy on Railway

1. Push this folder to a GitHub repository.
2. In Railway, create a new project and choose "Deploy from GitHub repo", select this repo.
3. In the Railway project's Variables tab, add:
   - `BOT_TOKEN` = your token from BotFather
4. Railway detects the `Dockerfile` and builds/runs the container automatically. No start command needed, it's set by `CMD` in the Dockerfile.
5. Deploy. Check the Deployments logs for "Bot starting..." to confirm it's running.

Note: this bot uses long polling (`run_polling`), not webhooks, so no public URL or port is needed on Railway.

## Adding more tracking parameters

Open `main.py` and edit:
- `TRACKING_PARAMS_EXACT` for exact parameter names.
- `TRACKING_PARAMS_PREFIX` for parameter name prefixes (like `utm_`).
- `DOMAIN_TRACKING_PARAMS` for parameters that should only be stripped on specific domains.
