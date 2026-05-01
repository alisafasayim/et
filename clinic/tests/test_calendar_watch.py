"""Calendar Watch (push) yardımcıları."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def cw(monkeypatch):
    monkeypatch.setenv("CALENDAR_PUSH_ENABLED", "true")
    monkeypatch.setenv("CALENDAR_PUSH_TOKEN", "tok-secret")
    monkeypatch.setenv("WEBHOOK_PUBLIC_URL", "https://klinik.example.com")

    import importlib
    import calendar_watch as mod
    importlib.reload(mod)
    return mod


def test_verify_push_token_passes_when_match(cw):
    assert cw.verify_push_token("tok-secret") is True


def test_verify_push_token_fails_on_mismatch(cw):
    assert cw.verify_push_token("wrong") is False


def test_verify_push_token_no_token_skip(monkeypatch):
    monkeypatch.delenv("CALENDAR_PUSH_TOKEN", raising=False)
    import importlib, calendar_watch
    importlib.reload(calendar_watch)
    assert calendar_watch.verify_push_token("anything") is True


def test_renewal_needed_true_when_expiration_close(cw):
    soon = (datetime.now(tz=timezone.utc) + timedelta(hours=2)).timestamp() * 1000
    assert cw.renewal_needed({"expiration_ms": int(soon)}) is True


def test_renewal_needed_false_when_far_in_future(cw):
    far = (datetime.now(tz=timezone.utc) + timedelta(days=5)).timestamp() * 1000
    assert cw.renewal_needed({"expiration_ms": int(far)}) is False


def test_renewal_needed_true_when_no_expiration(cw):
    assert cw.renewal_needed({}) is True


def test_handle_push_sync_state_returns_synced(cw):
    result = cw.handle_push_notification({"X-Goog-Resource-State": "sync"})
    assert result["status"] == "synced"


def test_handle_push_exists_state_triggers_poll(cw):
    """Yeni event bildirimi → poll_and_notify çağrılmalı."""
    fake_results = [{"status": "sent"}, {"status": "sent"}, {"status": "no_phone"}]
    pytest.importorskip("google.oauth2.credentials", reason="google-auth yüklü değil")
    with patch("module3_whatsapp_communicator.poll_and_notify", return_value=fake_results) as poll_mock:
        result = cw.handle_push_notification({"X-Goog-Resource-State": "exists"})
    poll_mock.assert_called_once()
    assert result["status"] == "ok"
    assert result["sent"] == 2


def test_start_watch_no_op_when_disabled(monkeypatch):
    monkeypatch.setenv("CALENDAR_PUSH_ENABLED", "false")
    import importlib, calendar_watch
    importlib.reload(calendar_watch)
    assert calendar_watch.start_watch() is None


def test_start_watch_rejects_non_https_url(monkeypatch):
    monkeypatch.setenv("CALENDAR_PUSH_ENABLED", "true")
    monkeypatch.setenv("WEBHOOK_PUBLIC_URL", "http://insecure.example.com")
    import importlib, calendar_watch
    importlib.reload(calendar_watch)
    assert calendar_watch.start_watch() is None


def test_save_and_load_watch_state_roundtrip(cw, tmp_path, monkeypatch):
    import state_store
    fresh = state_store.StateStore(tmp_path / "watch.db")
    monkeypatch.setattr(state_store, "_default_store", fresh)

    cw._save_watch_state("ch-1", "res-1", 1234567890)
    state = cw._load_watch_state()
    assert state["channel_id"] == "ch-1"
    assert state["resource_id"] == "res-1"
    assert state["expiration_ms"] == 1234567890
