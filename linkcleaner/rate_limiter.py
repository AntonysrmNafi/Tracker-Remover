"""Simple in-memory per-user sliding-window rate limit, to stop one user
from spamming the bot with links."""

import time
from collections import defaultdict, deque

RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 8

_user_request_times: dict[int, deque] = defaultdict(deque)


def is_rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    timestamps = _user_request_times[user_id]
    while timestamps and now - timestamps[0] > RATE_LIMIT_WINDOW_SECONDS:
        timestamps.popleft()
    if len(timestamps) >= RATE_LIMIT_MAX_REQUESTS:
        return True
    timestamps.append(now)
    return False
