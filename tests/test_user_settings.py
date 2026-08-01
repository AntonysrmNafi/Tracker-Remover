import pytest

import linkcleaner.stats_store as stats_store
import linkcleaner.user_settings as user_settings


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(stats_store, "DB_PATH", str(db_path))
    yield db_path


def test_format_privacy_mode_text_on():
    text = user_settings.format_privacy_mode_text(True)
    assert "🔒 ON" in text


def test_format_privacy_mode_text_off():
    text = user_settings.format_privacy_mode_text(False)
    assert "🔓 OFF" in text


async def test_get_privacy_mode_text_reflects_current_state():
    await stats_store.touch_user(1, "alice", "Alice")
    text = await user_settings.get_privacy_mode_text(1)
    assert "🔓 OFF" in text

    await stats_store.set_privacy_mode(1, True)
    text = await user_settings.get_privacy_mode_text(1)
    assert "🔒 ON" in text
