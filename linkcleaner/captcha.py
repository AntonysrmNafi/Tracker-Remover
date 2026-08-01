"""Captcha challenge generation, plus the sliding-window flood counter that
decides when a verified user must re-verify.

Verification *status* is persisted (see stats_store.is_captcha_verified /
set_captcha_verified); a pending, not-yet-answered challenge is in-memory
only (see linkcleaner.telegram_bot) since a lost challenge on restart just
means the user gets a fresh one next time — no real downside.
"""

import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass

FLOOD_WINDOW_SECONDS = 60
FLOOD_THRESHOLD = 5  # more than this many links within the window forces re-verification

_link_timestamps: dict[int, deque] = defaultdict(deque)


@dataclass
class CaptchaChallenge:
    question: str
    correct_answer: int
    choices: list[int]


def generate_challenge() -> CaptchaChallenge:
    a = random.randint(1, 20)
    b = random.randint(1, 20)
    correct = a + b
    question = f"What is {a} + {b}?"

    choices = {correct}
    offsets = [-3, -2, -1, 1, 2, 3]
    random.shuffle(offsets)
    for offset in offsets:
        if len(choices) >= 4:
            break
        candidate = correct + offset
        if candidate > 0:
            choices.add(candidate)

    choice_list = list(choices)
    random.shuffle(choice_list)
    return CaptchaChallenge(question=question, correct_answer=correct, choices=choice_list)


def check_and_record_flood(user_id: int, link_count: int) -> bool:
    """Records `link_count` links just sent by user_id, and returns True if
    they've now sent more than FLOOD_THRESHOLD links within the last
    FLOOD_WINDOW_SECONDS — i.e. captcha re-verification should be required."""
    now = time.monotonic()
    timestamps = _link_timestamps[user_id]
    while timestamps and now - timestamps[0] > FLOOD_WINDOW_SECONDS:
        timestamps.popleft()
    timestamps.extend([now] * link_count)
    return len(timestamps) > FLOOD_THRESHOLD
