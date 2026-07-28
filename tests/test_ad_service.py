import pytest
from telegram.error import Forbidden

import linkcleaner.ad_service as ad_service
import linkcleaner.ad_store as ad_store
import linkcleaner.stats_store as stats_store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(ad_store, "DB_PATH", str(db_path))
    monkeypatch.setattr(stats_store, "DB_PATH", str(db_path))
    yield db_path


class FakeBot:
    def __init__(self, fail_copy_for: set[int] | None = None, fail_delete_for: set[int] | None = None):
        self.fail_copy_for = fail_copy_for or set()
        self.fail_delete_for = fail_delete_for or set()
        self.copy_calls: list[tuple[int, object]] = []
        self.delete_calls: list[tuple[int, int]] = []

    async def copy_message(self, chat_id, from_chat_id, message_id, reply_markup=None):
        self.copy_calls.append((chat_id, reply_markup))
        if chat_id in self.fail_copy_for:
            raise Forbidden("bot was blocked by the user")
        return type("Result", (), {"message_id": 1000 + chat_id})()

    async def delete_message(self, chat_id, message_id):
        self.delete_calls.append((chat_id, message_id))
        if chat_id in self.fail_delete_for:
            raise Forbidden("message can't be deleted")


async def _seed_users(*user_ids):
    for uid in user_ids:
        await stats_store.touch_user(uid, f"user{uid}", f"User {uid}")


async def test_send_ad_delivers_to_every_known_user_and_records_deliveries():
    await _seed_users(1, 2, 3)
    ad_id = await ad_store.create_ad(-100, 42)
    await ad_store.update_ad_expire_hours(ad_id, 24)
    bot = FakeBot()

    sent = await ad_service.send_ad(bot, ad_id)

    assert sent == 3
    ad = await ad_store.get_ad(ad_id)
    assert ad.status == "sent"
    assert ad.sent_count == 3
    assert ad.expires_at is not None

    deliveries = await ad_store.get_active_deliveries(ad_id)
    assert {d.user_id for d in deliveries} == {1, 2, 3}


async def test_send_ad_attaches_button_when_present():
    await _seed_users(1)
    ad_id = await ad_store.create_ad(-100, 42)
    await ad_store.update_ad_button(ad_id, "Visit", "https://example.com")
    await ad_store.update_ad_expire_hours(ad_id, 1)
    bot = FakeBot()

    await ad_service.send_ad(bot, ad_id)

    assert len(bot.copy_calls) == 1
    _, markup = bot.copy_calls[0]
    assert markup is not None
    assert markup.inline_keyboard[0][0].text == "Visit"
    assert markup.inline_keyboard[0][0].url == "https://example.com"


async def test_send_ad_no_button_sends_no_markup():
    await _seed_users(1)
    ad_id = await ad_store.create_ad(-100, 42)
    await ad_store.update_ad_expire_hours(ad_id, 1)
    bot = FakeBot()

    await ad_service.send_ad(bot, ad_id)

    _, markup = bot.copy_calls[0]
    assert markup is None


async def test_send_ad_unknown_id_returns_zero():
    bot = FakeBot()
    assert await ad_service.send_ad(bot, 999999) == 0


async def test_expire_ad_deletes_all_active_deliveries_and_marks_expired():
    ad_id = await ad_store.create_ad(-100, 42)
    await ad_store.record_delivery(ad_id, 1, 501)
    await ad_store.record_delivery(ad_id, 2, 502)
    bot = FakeBot()

    await ad_service.expire_ad(bot, ad_id)

    assert set(bot.delete_calls) == {(1, 501), (2, 502)}
    assert await ad_store.get_active_deliveries(ad_id) == []
    ad = await ad_store.get_ad(ad_id)
    assert ad.status == "expired"


async def test_expire_ad_marks_delivery_deleted_even_if_delete_fails():
    ad_id = await ad_store.create_ad(-100, 42)
    await ad_store.record_delivery(ad_id, 1, 501)
    bot = FakeBot(fail_delete_for={1})

    await ad_service.expire_ad(bot, ad_id)

    # The message might already be gone (user deleted it, etc.) — that's
    # not something we can fix by retrying, so we still mark it handled.
    assert await ad_store.get_active_deliveries(ad_id) == []
    ad = await ad_store.get_ad(ad_id)
    assert ad.status == "expired"


async def test_send_ad_includes_blocked_users():
    await _seed_users(1, 2)
    await stats_store.block_user(2)
    ad_id = await ad_store.create_ad(-100, 42)
    await ad_store.update_ad_expire_hours(ad_id, 1)
    bot = FakeBot()

    sent = await ad_service.send_ad(bot, ad_id)

    assert sent == 2
    assert {c[0] for c in bot.copy_calls} == {1, 2}
