import pytest

import linkcleaner.broadcast_store as broadcast_store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_broadcasts.db"
    monkeypatch.setattr(broadcast_store, "DB_PATH", str(db_path))
    yield db_path


async def test_create_broadcast_returns_an_id():
    broadcast_id = await broadcast_store.create_broadcast("public", None, -100123, 42)
    assert isinstance(broadcast_id, int)
    assert broadcast_id > 0


async def test_created_broadcast_starts_pending():
    broadcast_id = await broadcast_store.create_broadcast("public", None, -100123, 42)
    broadcast = await broadcast_store.get_broadcast(broadcast_id)
    assert broadcast.status == "pending"
    assert broadcast.broadcast_type == "public"
    assert broadcast.target_user_id is None
    assert broadcast.source_chat_id == -100123
    assert broadcast.source_message_id == 42
    assert broadcast.sent_count == 0
    assert broadcast.failed_count == 0
    assert broadcast.completed_at is None


async def test_specific_broadcast_stores_target_user():
    broadcast_id = await broadcast_store.create_broadcast("specific", 555, -100123, 42)
    broadcast = await broadcast_store.get_broadcast(broadcast_id)
    assert broadcast.target_user_id == 555


async def test_mark_broadcast_result_updates_status_and_counts():
    broadcast_id = await broadcast_store.create_broadcast("public", None, -100123, 42)
    await broadcast_store.mark_broadcast_result(broadcast_id, "sent", 98, 2)

    broadcast = await broadcast_store.get_broadcast(broadcast_id)
    assert broadcast.status == "sent"
    assert broadcast.sent_count == 98
    assert broadcast.failed_count == 2
    assert broadcast.completed_at is not None


async def test_get_broadcast_returns_none_for_unknown_id():
    assert await broadcast_store.get_broadcast(999999) is None
