import pytest

import linkcleaner.settings_store as settings_store


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_settings.db"
    monkeypatch.setattr(settings_store, "DB_PATH", str(db_path))
    yield db_path


async def test_maintenance_defaults_off_with_default_message():
    state = await settings_store.get_maintenance_state()
    assert state.enabled is False
    assert state.message == settings_store.DEFAULT_MAINTENANCE_MESSAGE


async def test_set_maintenance_enabled_on_and_off():
    await settings_store.set_maintenance_enabled(True)
    assert (await settings_store.get_maintenance_state()).enabled is True

    await settings_store.set_maintenance_enabled(False)
    assert (await settings_store.get_maintenance_state()).enabled is False


async def test_set_maintenance_message():
    await settings_store.set_maintenance_message("Back soon!")
    state = await settings_store.get_maintenance_state()
    assert state.message == "Back soon!"


async def test_set_maintenance_message_truncates_over_limit():
    long_message = "x" * 3000
    await settings_store.set_maintenance_message(long_message)
    state = await settings_store.get_maintenance_state()
    assert len(state.message) == settings_store.MAX_MAINTENANCE_MESSAGE_LENGTH


async def test_maintenance_state_survives_multiple_reads():
    await settings_store.set_maintenance_enabled(True)
    await settings_store.set_maintenance_message("Custom message")

    state = await settings_store.get_maintenance_state()
    assert state.enabled is True
    assert state.message == "Custom message"
