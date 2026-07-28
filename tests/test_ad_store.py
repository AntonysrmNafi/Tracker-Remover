import time

import pytest

import linkcleaner.ad_store as ad_store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_ads.db"
    monkeypatch.setattr(ad_store, "DB_PATH", str(db_path))
    yield db_path


async def test_create_ad_starts_as_draft():
    ad_id = await ad_store.create_ad(-100123, 42)
    ad = await ad_store.get_ad(ad_id)
    assert ad.status == "draft"
    assert ad.source_chat_id == -100123
    assert ad.source_message_id == 42
    assert ad.button_text is None
    assert ad.button_url is None
    assert ad.expire_hours == 0
    assert ad.sent_at is None
    assert ad.expires_at is None


async def test_update_ad_button():
    ad_id = await ad_store.create_ad(-100123, 42)
    await ad_store.update_ad_button(ad_id, "Visit site", "https://example.com")

    ad = await ad_store.get_ad(ad_id)
    assert ad.button_text == "Visit site"
    assert ad.button_url == "https://example.com"


async def test_update_ad_expire_hours():
    ad_id = await ad_store.create_ad(-100123, 42)
    await ad_store.update_ad_expire_hours(ad_id, 24)

    ad = await ad_store.get_ad(ad_id)
    assert ad.expire_hours == 24


async def test_mark_ad_sent_sets_status_and_expiry():
    ad_id = await ad_store.create_ad(-100123, 42)
    expires_at = time.time() + 3600
    await ad_store.mark_ad_sent(ad_id, 50, expires_at)

    ad = await ad_store.get_ad(ad_id)
    assert ad.status == "sent"
    assert ad.sent_count == 50
    assert ad.expires_at == expires_at
    assert ad.sent_at is not None


async def test_mark_ad_cancelled():
    ad_id = await ad_store.create_ad(-100123, 42)
    await ad_store.mark_ad_cancelled(ad_id)
    ad = await ad_store.get_ad(ad_id)
    assert ad.status == "cancelled"


async def test_mark_ad_expired():
    ad_id = await ad_store.create_ad(-100123, 42)
    await ad_store.mark_ad_expired(ad_id)
    ad = await ad_store.get_ad(ad_id)
    assert ad.status == "expired"


async def test_get_ad_returns_none_for_unknown_id():
    assert await ad_store.get_ad(999999) is None


async def test_record_and_list_active_deliveries():
    ad_id = await ad_store.create_ad(-100123, 42)
    await ad_store.record_delivery(ad_id, 1, 501)
    await ad_store.record_delivery(ad_id, 2, 502)

    deliveries = await ad_store.get_active_deliveries(ad_id)
    assert {d.user_id for d in deliveries} == {1, 2}
    assert all(d.deleted is False for d in deliveries)


async def test_mark_delivery_deleted_excludes_it_from_active_list():
    ad_id = await ad_store.create_ad(-100123, 42)
    await ad_store.record_delivery(ad_id, 1, 501)
    deliveries = await ad_store.get_active_deliveries(ad_id)
    delivery_id = deliveries[0].id

    await ad_store.mark_delivery_deleted(delivery_id)

    assert await ad_store.get_active_deliveries(ad_id) == []


async def test_get_pending_expiry_ads_only_includes_sent_ads_with_expiry():
    draft_ad = await ad_store.create_ad(-100123, 1)

    sent_ad = await ad_store.create_ad(-100123, 2)
    await ad_store.mark_ad_sent(sent_ad, 10, time.time() + 3600)

    cancelled_ad = await ad_store.create_ad(-100123, 3)
    await ad_store.mark_ad_cancelled(cancelled_ad)

    expired_ad = await ad_store.create_ad(-100123, 4)
    await ad_store.mark_ad_sent(expired_ad, 5, time.time() - 10)
    await ad_store.mark_ad_expired(expired_ad)

    pending = await ad_store.get_pending_expiry_ads()
    pending_ids = {ad.id for ad in pending}

    assert pending_ids == {sent_ad}
    assert draft_ad not in pending_ids
    assert cancelled_ad not in pending_ids
    assert expired_ad not in pending_ids
