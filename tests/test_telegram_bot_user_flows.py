"""Integration tests for telegram_bot.py's user-facing flows: Settings /
Privacy Mode, captcha verification (including flood-triggered
re-verification), and maintenance-mode gating. Uses the same lightweight
fake-object approach as test_telegram_bot_admin_flows.py."""

import pytest
from telegram.constants import ChatType

import linkcleaner.captcha as captcha
import linkcleaner.settings_store as settings_store
import linkcleaner.stats_store as stats_store
import linkcleaner.telegram_bot as telegram_bot


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(stats_store, "DB_PATH", str(db_path))
    monkeypatch.setattr(settings_store, "DB_PATH", str(db_path))
    yield db_path


@pytest.fixture(autouse=True)
def clean_in_memory_state():
    telegram_bot._pending_captcha.clear()
    captcha._link_timestamps.clear()
    yield
    telegram_bot._pending_captcha.clear()
    captcha._link_timestamps.clear()


class FakeUser:
    def __init__(self, user_id, username=None, first_name="Alice"):
        self.id = user_id
        self.username = username
        self.first_name = first_name


class FakeChat:
    def __init__(self, chat_id, chat_type=ChatType.PRIVATE):
        self.id = chat_id
        self.type = chat_type


class FakeMessage:
    def __init__(self, chat, message_id, text=None, from_user=None):
        self.chat = chat
        self.message_id = message_id
        self.text = text
        self.caption = None
        self.from_user = from_user
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append((text, reply_markup))
        return FakeMessage(self.chat, self.message_id + 1000, text=text, from_user=self.from_user)


class FakeCallbackQuery:
    def __init__(self, data, from_user, message):
        self.data = data
        self.from_user = from_user
        self.message = message
        self.answered = False
        self.answer_text = None
        self.show_alert = False

    async def answer(self, text=None, show_alert=False):
        self.answered = True
        self.answer_text = text
        self.show_alert = show_alert


class FakeContext:
    pass


def _callback_update(data, user, message):
    query = FakeCallbackQuery(data, user, message)
    update = type("Update", (), {"callback_query": query, "effective_message": None})()
    return update, query


def _message_update(message):
    return type("Update", (), {"message": message, "effective_message": message})()


# ---------------------------------------------------------------------------
# Settings / Privacy Mode
# ---------------------------------------------------------------------------
async def test_settings_button_shows_privacy_mode_option():
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1)
    update, _ = _callback_update("show_settings", user, msg)

    await telegram_bot.handle_user_settings_callback(update, FakeContext())

    assert msg.replies[-1][1] == telegram_bot.SETTINGS_KEYBOARD


async def test_privacy_mode_starts_off():
    await stats_store.touch_user(1, "alice", "Alice")
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1)
    update, _ = _callback_update("show_privacy_mode", user, msg)

    await telegram_bot.handle_user_settings_callback(update, FakeContext())

    assert "🔓 OFF" in msg.replies[-1][0]


async def test_toggle_privacy_mode_on_then_off():
    await stats_store.touch_user(1, "alice", "Alice")
    user = FakeUser(1)

    msg1 = FakeMessage(FakeChat(1), 1)
    update, _ = _callback_update("toggle_privacy_mode", user, msg1)
    await telegram_bot.handle_user_settings_callback(update, FakeContext())
    assert await stats_store.is_privacy_mode_enabled(1) is True
    assert "🔒 ON" in msg1.replies[-1][0]

    msg2 = FakeMessage(FakeChat(1), 2)
    update, _ = _callback_update("toggle_privacy_mode", user, msg2)
    await telegram_bot.handle_user_settings_callback(update, FakeContext())
    assert await stats_store.is_privacy_mode_enabled(1) is False
    assert "🔓 OFF" in msg2.replies[-1][0]


async def test_settings_back_shows_main_menu():
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1)
    update, _ = _callback_update("settings_back", user, msg)

    await telegram_bot.handle_user_settings_callback(update, FakeContext())

    assert msg.replies[-1][1] == telegram_bot.USER_MAIN_KEYBOARD


async def test_blocked_user_cannot_open_settings():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.block_user(1)
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1)
    update, query = _callback_update("show_settings", user, msg)

    await telegram_bot.handle_user_settings_callback(update, FakeContext())

    assert query.show_alert is True
    assert msg.replies == []


async def test_settings_callback_ignored_in_group_chat():
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(-100, chat_type=ChatType.SUPERGROUP), 1)
    update, query = _callback_update("show_settings", user, msg)

    await telegram_bot.handle_user_settings_callback(update, FakeContext())

    assert query.answered is False
    assert msg.replies == []


# ---------------------------------------------------------------------------
# Captcha
# ---------------------------------------------------------------------------
async def test_new_user_start_gets_captcha_challenge():
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1, from_user=user)
    update = type("Update", (), {"effective_chat": msg.chat, "effective_user": user, "message": msg})()

    await telegram_bot.start(update, FakeContext())

    assert 1 in telegram_bot._pending_captcha
    challenge_reply = msg.replies[-1]
    assert "verify" in challenge_reply[0].lower()
    assert challenge_reply[1] is not None  # has the answer-choice keyboard


async def test_unverified_user_cannot_clean_links():
    await stats_store.touch_user(1, "alice", "Alice")
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1, text="https://example.com", from_user=user)

    await telegram_bot.handle_message(_message_update(msg), FakeContext())

    assert 1 in telegram_bot._pending_captcha
    assert "verify" in msg.replies[-1][0].lower()


async def test_correct_captcha_answer_verifies_user():
    await stats_store.touch_user(1, "alice", "Alice")
    telegram_bot._pending_captcha[1] = 7
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1)
    update, _ = _callback_update("captcha:7", user, msg)

    await telegram_bot.handle_captcha_callback(update, FakeContext())

    assert await stats_store.is_captcha_verified(1) is True
    assert 1 not in telegram_bot._pending_captcha
    assert "Verified" in msg.replies[-1][0]


async def test_wrong_captcha_answer_issues_new_challenge():
    await stats_store.touch_user(1, "alice", "Alice")
    telegram_bot._pending_captcha[1] = 7
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1)
    update, _ = _callback_update("captcha:3", user, msg)

    await telegram_bot.handle_captcha_callback(update, FakeContext())

    assert await stats_store.is_captcha_verified(1) is False
    assert 1 in telegram_bot._pending_captcha  # a fresh one was issued
    assert "Incorrect" in msg.replies[0][0]


async def test_expired_captcha_callback_is_handled_gracefully():
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1)
    update, _ = _callback_update("captcha:7", user, msg)

    await telegram_bot.handle_captcha_callback(update, FakeContext())

    assert "expired" in msg.replies[-1][0].lower()


async def test_verified_user_can_clean_links():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.set_captcha_verified(1, True)
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1, text="https://www.youtube.com/watch?v=x&si=y", from_user=user)

    await telegram_bot.handle_message(_message_update(msg), FakeContext())

    assert "Clean & Secure Link" in msg.replies[-1][0]


async def test_flooding_revokes_verification_and_requires_recaptcha():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.set_captcha_verified(1, True)
    user = FakeUser(1)

    # Send 6 links in one message — over the 5/min threshold.
    links = " ".join(f"https://example.com/{i}" for i in range(6))
    msg = FakeMessage(FakeChat(1), 1, text=links, from_user=user)

    await telegram_bot.handle_message(_message_update(msg), FakeContext())

    assert await stats_store.is_captcha_verified(1) is False
    assert 1 in telegram_bot._pending_captcha
    assert "quickly" in msg.replies[-1][0].lower()


async def test_under_threshold_does_not_trigger_recaptcha():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.set_captcha_verified(1, True)
    user = FakeUser(1)

    links = " ".join(f"https://example.com/{i}" for i in range(5))
    msg = FakeMessage(FakeChat(1), 1, text=links, from_user=user)

    await telegram_bot.handle_message(_message_update(msg), FakeContext())

    assert await stats_store.is_captcha_verified(1) is True
    assert "Clean & Secure Link" in msg.replies[-1][0]


# ---------------------------------------------------------------------------
# Maintenance gate
# ---------------------------------------------------------------------------
async def test_maintenance_blocks_link_cleaning():
    await settings_store.set_maintenance_enabled(True)
    await settings_store.set_maintenance_message("Down for repairs.")
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.set_captcha_verified(1, True)
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1, text="https://example.com", from_user=user)

    await telegram_bot.handle_message(_message_update(msg), FakeContext())

    assert msg.replies == [("Down for repairs.", None)]


async def test_maintenance_blocks_plain_text_too():
    await settings_store.set_maintenance_enabled(True)
    await settings_store.set_maintenance_message("Down for repairs.")
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1, text="hello, is anyone there?", from_user=user)

    await telegram_bot.handle_message(_message_update(msg), FakeContext())

    assert msg.replies == [("Down for repairs.", None)]


async def test_maintenance_blocks_start_command():
    await settings_store.set_maintenance_enabled(True)
    await settings_store.set_maintenance_message("Down for repairs.")
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1, from_user=user)
    update = type("Update", (), {"effective_chat": msg.chat, "effective_user": user, "message": msg})()

    await telegram_bot.start(update, FakeContext())

    assert msg.replies == [("Down for repairs.", None)]


async def test_maintenance_off_does_not_affect_normal_use():
    await stats_store.touch_user(1, "alice", "Alice")
    await stats_store.set_captcha_verified(1, True)
    user = FakeUser(1)
    msg = FakeMessage(FakeChat(1), 1, text="https://example.com", from_user=user)

    await telegram_bot.handle_message(_message_update(msg), FakeContext())

    assert "Clean & Secure Link" in msg.replies[-1][0]
