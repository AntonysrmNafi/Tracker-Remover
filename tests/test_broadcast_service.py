import pytest
from telegram.error import Forbidden

import linkcleaner.broadcast_service as broadcast_service
import linkcleaner.broadcast_store as broadcast_store
import linkcleaner.stats_store as stats_store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(broadcast_store, "DB_PATH", str(db_path))
    monkeypatch.setattr(stats_store, "DB_PATH", str(db_path))
    yield db_path


class FakeBot:
    """Minimal stand-in for telegram.Bot: records copy_message calls and can
    be told to fail for specific chat_ids."""

    def __init__(self, fail_for: set[int] | None = None):
        self.fail_for = fail_for or set()
        self.calls: list[int] = []

    async def copy_message(self, chat_id, from_chat_id, message_id, reply_markup=None):
        self.calls.append(chat_id)
        if chat_id in self.fail_for:
            raise Forbidden("bot was blocked by the user")
        return type("Result", (), {"message_id": 1000 + chat_id})()


async def _seed_users(*user_ids):
    for uid in user_ids:
        await stats_store.touch_user(uid, f"user{uid}", f"User {uid}")


async def test_send_public_broadcast_reaches_every_known_user():
    await _seed_users(1, 2, 3)
    broadcast_id = await broadcast_store.create_broadcast("public", None, -100, 42)
    bot = FakeBot()

    sent, failed = await broadcast_service.send_public_broadcast(bot, broadcast_id, -100, 42)

    assert sent == 3
    assert failed == 0
    assert set(bot.calls) == {1, 2, 3}


async def test_send_public_broadcast_includes_blocked_users():
    await _seed_users(1, 2)
    await stats_store.block_user(2)
    broadcast_id = await broadcast_store.create_broadcast("public", None, -100, 42)
    bot = FakeBot()

    sent, failed = await broadcast_service.send_public_broadcast(bot, broadcast_id, -100, 42)

    assert sent == 2
    assert set(bot.calls) == {1, 2}


async def test_send_public_broadcast_counts_failures_and_updates_record():
    await _seed_users(1, 2, 3)
    broadcast_id = await broadcast_store.create_broadcast("public", None, -100, 42)
    bot = FakeBot(fail_for={2})

    sent, failed = await broadcast_service.send_public_broadcast(bot, broadcast_id, -100, 42)

    assert sent == 2
    assert failed == 1

    record = await broadcast_store.get_broadcast(broadcast_id)
    assert record.status == "sent"
    assert record.sent_count == 2
    assert record.failed_count == 1
    assert record.completed_at is not None


async def test_send_specific_broadcast_success():
    broadcast_id = await broadcast_store.create_broadcast("specific", 42, -100, 7)
    bot = FakeBot()

    ok = await broadcast_service.send_specific_broadcast(bot, broadcast_id, 42, -100, 7)

    assert ok is True
    assert bot.calls == [42]
    record = await broadcast_store.get_broadcast(broadcast_id)
    assert record.status == "sent"
    assert record.sent_count == 1


async def test_send_specific_broadcast_failure():
    broadcast_id = await broadcast_store.create_broadcast("specific", 42, -100, 7)
    bot = FakeBot(fail_for={42})

    ok = await broadcast_service.send_specific_broadcast(bot, broadcast_id, 42, -100, 7)

    assert ok is False
    record = await broadcast_store.get_broadcast(broadcast_id)
    assert record.status == "failed"
    assert record.failed_count == 1
