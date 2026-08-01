"""These tests exercise telegram_bot.py's admin-flow state machine directly,
using minimal fake stand-ins for python-telegram-bot's Update/Message/
CallbackQuery objects (only the attributes/methods our handlers actually
use). This is a deliberate exception to "telegram_bot.py isn't unit tested"
— the Broadcast/AD conversation logic is complex enough that direct
coverage is worth the extra test-double maintenance."""

import pytest
from telegram.constants import ChatType

import linkcleaner.ad_store as ad_store
import linkcleaner.broadcast_store as broadcast_store
import linkcleaner.settings_store as settings_store
import linkcleaner.stats_store as stats_store
import linkcleaner.telegram_bot as telegram_bot

ADMIN_GROUP_ID = -100999


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(stats_store, "DB_PATH", str(db_path))
    monkeypatch.setattr(broadcast_store, "DB_PATH", str(db_path))
    monkeypatch.setattr(ad_store, "DB_PATH", str(db_path))
    monkeypatch.setattr(settings_store, "DB_PATH", str(db_path))
    yield db_path


@pytest.fixture(autouse=True)
def admin_group(monkeypatch):
    monkeypatch.setattr(telegram_bot, "ADMIN_GROUP_ID", ADMIN_GROUP_ID)
    telegram_bot._admin_state.clear()
    yield
    telegram_bot._admin_state.clear()


class FakeUser:
    def __init__(self, user_id, username=None, first_name="Admin"):
        self.id = user_id
        self.username = username
        self.first_name = first_name


class FakeChat:
    def __init__(self, chat_id, chat_type=ChatType.SUPERGROUP):
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
        self.documents: list[object] = []

    async def reply_text(self, text, reply_markup=None, **kwargs):
        self.replies.append((text, reply_markup))
        return FakeMessage(self.chat, self.message_id + 1000, text=text, from_user=self.from_user)

    async def reply_document(self, document, **kwargs):
        self.documents.append(document)


class FakeCallbackQuery:
    def __init__(self, data, from_user, message):
        self.data = data
        self.from_user = from_user
        self.message = message
        self.answered = False
        self.answer_text = None

    async def answer(self, text=None, show_alert=False):
        self.answered = True
        self.answer_text = text

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        # Recorded in the same sink as reply_text so existing assertions
        # against message.replies[-1] keep working regardless of whether a
        # given response was an edit or a fresh message.
        self.message.replies.append((text, reply_markup))


class FakeJobQueue:
    def __init__(self):
        self.scheduled: list[dict] = []

    def run_once(self, callback, when, data=None, name=None):
        self.scheduled.append({"callback": callback, "when": when, "data": data, "name": name})


class FakeBot:
    def __init__(self, fail_for=None):
        self.fail_for = fail_for or set()
        self.copy_calls: list[int] = []

    async def copy_message(self, chat_id, from_chat_id, message_id, reply_markup=None):
        self.copy_calls.append(chat_id)
        if chat_id in self.fail_for:
            from telegram.error import Forbidden
            raise Forbidden("blocked")
        return type("Result", (), {"message_id": 5000 + chat_id})()

    async def delete_message(self, chat_id, message_id):
        pass


class FakeContext:
    def __init__(self, bot=None, job_queue=None):
        self.bot = bot or FakeBot()
        self.job_queue = job_queue if job_queue is not None else FakeJobQueue()


def _callback_update(data, admin_id, message):
    query = FakeCallbackQuery(data, FakeUser(admin_id), message)
    update = type("Update", (), {"callback_query": query, "effective_message": None})()
    return update, query


def _message_update(message):
    return type("Update", (), {"message": message, "effective_message": message})()


async def _seed_users(*user_ids):
    for uid in user_ids:
        await stats_store.touch_user(uid, f"user{uid}", f"User {uid}")


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
async def test_admin_callback_ignored_outside_admin_group():
    chat = FakeChat(-100111)  # not the admin group
    message = FakeMessage(chat, 1)
    update, query = _callback_update("admin:statistics", 1, message)

    await telegram_bot.handle_admin_callback(update, FakeContext())

    assert query.answered is False
    assert message.replies == []


async def test_admin_group_message_ignored_with_no_pending_state():
    chat = FakeChat(ADMIN_GROUP_ID)
    admin = FakeUser(1)
    message = FakeMessage(chat, 1, text="12345", from_user=admin)
    update = _message_update(message)

    await telegram_bot.handle_admin_group_message(update, FakeContext())

    assert message.replies == []


# ---------------------------------------------------------------------------
# Public broadcast flow
# ---------------------------------------------------------------------------
async def test_public_broadcast_full_flow():
    await _seed_users(10, 20, 30)
    chat = FakeChat(ADMIN_GROUP_ID)
    admin = FakeUser(1)

    # tap "Broadcast"
    msg1 = FakeMessage(chat, 1)
    update, _ = _callback_update("admin:broadcast", 1, msg1)
    await telegram_bot.handle_admin_callback(update, FakeContext())
    assert msg1.replies[-1][1] == telegram_bot.BROADCAST_KEYBOARD

    # tap "Public Broadcast"
    msg2 = FakeMessage(chat, 2)
    update, _ = _callback_update("admin:broadcast_public", 1, msg2)
    await telegram_bot.handle_admin_callback(update, FakeContext())
    assert telegram_bot._admin_state[1] == {"flow": "broadcast", "mode": "public", "step": "content"}

    # send content
    content_msg = FakeMessage(chat, 3, text="Hello everyone!", from_user=admin)
    update = _message_update(content_msg)
    bot = FakeBot()
    await telegram_bot.handle_admin_group_message(update, FakeContext(bot=bot))

    assert 1 not in telegram_bot._admin_state
    assert set(bot.copy_calls) == {10, 20, 30}
    assert "Broadcasting to 3 users" in content_msg.replies[0][0]
    assert "Sent: 3, Failed: 0" in content_msg.replies[-1][0]


async def test_specific_broadcast_full_flow():
    await _seed_users(42)
    chat = FakeChat(ADMIN_GROUP_ID)
    admin = FakeUser(1)

    msg1 = FakeMessage(chat, 1)
    update, _ = _callback_update("admin:broadcast_specific", 1, msg1)
    await telegram_bot.handle_admin_callback(update, FakeContext())

    target_msg = FakeMessage(chat, 2, text="42", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(target_msg), FakeContext())
    assert telegram_bot._admin_state[1]["target_user_id"] == 42
    assert telegram_bot._admin_state[1]["step"] == "content"

    content_msg = FakeMessage(chat, 3, text="Just for you", from_user=admin)
    bot = FakeBot()
    await telegram_bot.handle_admin_group_message(_message_update(content_msg), FakeContext(bot=bot))

    assert 1 not in telegram_bot._admin_state
    assert bot.copy_calls == [42]
    assert "Sent to user 42" in content_msg.replies[-1][0]


async def test_specific_broadcast_unknown_target_aborts():
    chat = FakeChat(ADMIN_GROUP_ID)
    admin = FakeUser(1)

    msg1 = FakeMessage(chat, 1)
    update, _ = _callback_update("admin:broadcast_specific", 1, msg1)
    await telegram_bot.handle_admin_callback(update, FakeContext())

    target_msg = FakeMessage(chat, 2, text="999999", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(target_msg), FakeContext())

    assert 1 not in telegram_bot._admin_state
    assert "Could not find that user" in target_msg.replies[-1][0]


# ---------------------------------------------------------------------------
# AD flow
# ---------------------------------------------------------------------------
async def test_ad_flow_skip_button_and_send():
    await _seed_users(1, 2)
    chat = FakeChat(ADMIN_GROUP_ID)
    admin = FakeUser(9)

    msg1 = FakeMessage(chat, 1)
    update, _ = _callback_update("admin:ad_create", 9, msg1)
    await telegram_bot.handle_admin_callback(update, FakeContext())
    assert telegram_bot._admin_state[9]["step"] == "pin_choice"

    pin_msg = FakeMessage(chat, 2)
    update, _ = _callback_update("admin:ad_pin_no", 9, pin_msg)
    await telegram_bot.handle_admin_callback(update, FakeContext())
    assert telegram_bot._admin_state[9]["step"] == "content"

    content_msg = FakeMessage(chat, 3, text="Buy now!", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(content_msg), FakeContext())
    ad_id = telegram_bot._admin_state[9]["ad_id"]
    ad = await ad_store.get_ad(ad_id)
    assert ad.status == "draft"
    assert ad.pinned is False

    skip_msg = FakeMessage(chat, 4)
    update, _ = _callback_update("admin:ad_skip_button", 9, skip_msg)
    await telegram_bot.handle_admin_callback(update, FakeContext())
    assert telegram_bot._admin_state[9]["step"] == "hours"

    hours_msg = FakeMessage(chat, 5, text="24", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(hours_msg), FakeContext())
    assert telegram_bot._admin_state[9]["step"] == "confirm"
    assert "Auto-deletes after : 24h" in hours_msg.replies[-1][0]

    send_msg = FakeMessage(chat, 6)
    job_queue = FakeJobQueue()
    bot = FakeBot()
    update, _ = _callback_update("admin:ad_send", 9, send_msg)
    await telegram_bot.handle_admin_callback(update, FakeContext(bot=bot, job_queue=job_queue))

    assert 9 not in telegram_bot._admin_state
    ad = await ad_store.get_ad(ad_id)
    assert ad.status == "sent"
    assert ad.sent_count == 2
    assert len(job_queue.scheduled) == 1
    assert job_queue.scheduled[0]["data"] == {"ad_id": ad_id}
    assert job_queue.scheduled[0]["when"] == 24 * 3600


async def test_ad_flow_with_button():
    await _seed_users(1)
    chat = FakeChat(ADMIN_GROUP_ID)
    admin = FakeUser(9)

    msg1 = FakeMessage(chat, 1)
    update, _ = _callback_update("admin:ad_create", 9, msg1)
    await telegram_bot.handle_admin_callback(update, FakeContext())

    pin_msg = FakeMessage(chat, 2)
    update, _ = _callback_update("admin:ad_pin_yes", 9, pin_msg)
    await telegram_bot.handle_admin_callback(update, FakeContext())

    content_msg = FakeMessage(chat, 3, text="Ad content", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(content_msg), FakeContext())
    ad_id = telegram_bot._admin_state[9]["ad_id"]
    assert (await ad_store.get_ad(ad_id)).pinned is True

    add_btn_msg = FakeMessage(chat, 4)
    update, _ = _callback_update("admin:ad_add_button", 9, add_btn_msg)
    await telegram_bot.handle_admin_callback(update, FakeContext())

    name_msg = FakeMessage(chat, 5, text="Shop Now", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(name_msg), FakeContext())

    bad_link_msg = FakeMessage(chat, 6, text="not a link", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(bad_link_msg), FakeContext())
    assert "doesn't look like a valid link" in bad_link_msg.replies[-1][0]
    assert telegram_bot._admin_state[9]["step"] == "button_link"  # stayed on this step

    good_link_msg = FakeMessage(chat, 7, text="https://example.com/shop", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(good_link_msg), FakeContext())
    assert telegram_bot._admin_state[9]["step"] == "hours"

    ad = await ad_store.get_ad(ad_id)
    assert ad.button_text == "Shop Now"
    assert ad.button_url == "https://example.com/shop"

    hours_msg = FakeMessage(chat, 8, text="1", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(hours_msg), FakeContext())
    assert "Button : Shop Now → https://example.com/shop" in hours_msg.replies[-1][0]


async def test_ad_flow_invalid_hours_stays_on_step():
    await _seed_users(1)
    chat = FakeChat(ADMIN_GROUP_ID)
    admin = FakeUser(9)

    telegram_bot._admin_state[9] = {"flow": "ad", "step": "hours", "ad_id": await ad_store.create_ad(chat.id, 1)}

    bad_hours_msg = FakeMessage(chat, 2, text="500", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(bad_hours_msg), FakeContext())

    assert telegram_bot._admin_state[9]["step"] == "hours"
    assert "1 to 70" in bad_hours_msg.replies[-1][0]


async def test_ad_flow_cancel_marks_ad_cancelled():
    chat = FakeChat(ADMIN_GROUP_ID)
    ad_id = await ad_store.create_ad(chat.id, 1)
    telegram_bot._admin_state[9] = {"flow": "ad", "step": "button_choice", "ad_id": ad_id}

    cancel_msg = FakeMessage(chat, 2)
    update, _ = _callback_update("admin:ad_cancel", 9, cancel_msg)
    await telegram_bot.handle_admin_callback(update, FakeContext())

    assert 9 not in telegram_bot._admin_state
    ad = await ad_store.get_ad(ad_id)
    assert ad.status == "cancelled"
    assert cancel_msg.replies[-1][1] == telegram_bot.ADMIN_MAIN_KEYBOARD


async def test_ad_send_without_confirm_state_is_rejected():
    chat = FakeChat(ADMIN_GROUP_ID)
    send_msg = FakeMessage(chat, 1)
    update, _ = _callback_update("admin:ad_send", 9, send_msg)

    await telegram_bot.handle_admin_callback(update, FakeContext())

    assert "start over" in send_msg.replies[-1][0]


# ---------------------------------------------------------------------------
# Block enforcement (regular user path)
# ---------------------------------------------------------------------------
async def test_blocked_user_gets_blocked_message_instead_of_cleaning(monkeypatch):
    await stats_store.block_user(55)

    async def _fail_process_url(url):
        raise AssertionError("process_url must not be called for a blocked user")

    monkeypatch.setattr(telegram_bot, "process_url", _fail_process_url)

    chat = FakeChat(123, chat_type=ChatType.PRIVATE)
    user = FakeUser(55)
    message = FakeMessage(chat, 1, text="https://example.com", from_user=user)

    await telegram_bot.handle_message(_message_update(message), FakeContext())

    assert message.replies == [(telegram_bot.BLOCKED_MESSAGE, None)]


# ---------------------------------------------------------------------------
# Maintenance submenu
# ---------------------------------------------------------------------------
async def test_maintenance_button_shows_status():
    chat = FakeChat(ADMIN_GROUP_ID)
    msg = FakeMessage(chat, 1)
    update, _ = _callback_update("admin:maintenance", 1, msg)

    await telegram_bot.handle_admin_callback(update, FakeContext())

    assert "⚪ OFF" in msg.replies[-1][0]


async def test_maintenance_toggle_turns_on_then_off():
    chat = FakeChat(ADMIN_GROUP_ID)

    msg1 = FakeMessage(chat, 1)
    update, _ = _callback_update("admin:maintenance_toggle", 1, msg1)
    await telegram_bot.handle_admin_callback(update, FakeContext())
    assert (await settings_store.get_maintenance_state()).enabled is True
    assert "🟢 ON" in msg1.replies[-1][0]

    msg2 = FakeMessage(chat, 2)
    update, _ = _callback_update("admin:maintenance_toggle", 1, msg2)
    await telegram_bot.handle_admin_callback(update, FakeContext())
    assert (await settings_store.get_maintenance_state()).enabled is False
    assert "⚪ OFF" in msg2.replies[-1][0]


async def test_maintenance_set_message_flow():
    chat = FakeChat(ADMIN_GROUP_ID)
    admin = FakeUser(1)

    msg1 = FakeMessage(chat, 1)
    update, _ = _callback_update("admin:maintenance_set_message", 1, msg1)
    await telegram_bot.handle_admin_callback(update, FakeContext())
    assert telegram_bot._admin_state[1] == {"flow": "maintenance_message"}

    text_msg = FakeMessage(chat, 2, text="We'll be back in 30 minutes.", from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(text_msg), FakeContext())

    assert 1 not in telegram_bot._admin_state
    state = await settings_store.get_maintenance_state()
    assert state.message == "We'll be back in 30 minutes."
    assert "updated" in text_msg.replies[-1][0].lower()


async def test_maintenance_set_message_rejects_over_limit():
    chat = FakeChat(ADMIN_GROUP_ID)
    admin = FakeUser(1)
    telegram_bot._admin_state[1] = {"flow": "maintenance_message"}

    too_long_msg = FakeMessage(chat, 1, text="x" * 2001, from_user=admin)
    await telegram_bot.handle_admin_group_message(_message_update(too_long_msg), FakeContext())

    assert 1 not in telegram_bot._admin_state
    assert "invalid" in too_long_msg.replies[-1][0].lower()


async def test_maintenance_back_returns_to_main_menu():
    chat = FakeChat(ADMIN_GROUP_ID)
    msg = FakeMessage(chat, 1)
    update, _ = _callback_update("admin:maintenance_back", 1, msg)

    await telegram_bot.handle_admin_callback(update, FakeContext())

    assert msg.replies[-1][1] == telegram_bot.ADMIN_MAIN_KEYBOARD
