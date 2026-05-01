"""İptal akışı: Calendar slot temizleme + doktor bildirimi.

M3 google-auth ve Flask import eder; lokalde paket yoksa skip.
"""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# M3'ün ağır bağımlılıkları yoksa tüm dosyayı skip
pytest.importorskip("google.oauth2.credentials", reason="google-auth yüklü değil")


@pytest.fixture
def m3():
    import module3_whatsapp_communicator as mod
    return mod


def _make_appt(event_id: str, phone: str, start: datetime) -> dict:
    return {
        "event_id": event_id,
        "summary": "Hasta",
        "description": f"Tel: {phone}",
        "start": start.isoformat(),
        "start_dt": start,
        "phone": phone,
        "patient_name": "Hasta",
    }


def test_find_upcoming_returns_match_by_normalized_phone(m3):
    now = datetime.now(tz=timezone.utc)
    appts = [
        _make_appt("evt-other", "5559999999", now + timedelta(hours=2)),
        _make_appt("evt-target", "0532 123 45 67", now + timedelta(days=1)),
    ]
    with patch.object(m3, "fetch_upcoming_appointments", return_value=appts), \
         patch.object(m3, "get_calendar_service", return_value=MagicMock()):
        result = m3.find_upcoming_appointment_by_phone("905321234567")
    assert result is not None
    assert result["event_id"] == "evt-target"


def test_find_upcoming_returns_none_when_no_match(m3):
    now = datetime.now(tz=timezone.utc)
    appts = [_make_appt("evt-1", "5559999999", now + timedelta(hours=2))]
    with patch.object(m3, "fetch_upcoming_appointments", return_value=appts), \
         patch.object(m3, "get_calendar_service", return_value=MagicMock()):
        result = m3.find_upcoming_appointment_by_phone("905321234567")
    assert result is None


def test_find_upcoming_picks_earliest_when_multiple_match(m3):
    """Aynı veliye birden fazla yaklaşan randevu varsa EN YAKININI seç."""
    now = datetime.now(tz=timezone.utc)
    later = _make_appt("evt-later", "05321234567", now + timedelta(days=5))
    earlier = _make_appt("evt-earlier", "05321234567", now + timedelta(hours=3))
    with patch.object(m3, "fetch_upcoming_appointments", return_value=[later, earlier]), \
         patch.object(m3, "get_calendar_service", return_value=MagicMock()):
        result = m3.find_upcoming_appointment_by_phone("905321234567")
    assert result["event_id"] == "evt-earlier"


def test_delete_calendar_event_no_op_when_disabled(monkeypatch, m3):
    """CALENDAR_AUTO_DELETE_ON_CANCEL=false → silme atlanır."""
    monkeypatch.setattr(m3, "CALENDAR_AUTO_DELETE_ON_CANCEL", False)
    assert m3.delete_calendar_event("any-event") is False


def test_delete_calendar_event_calls_api_when_enabled(monkeypatch, m3):
    monkeypatch.setattr(m3, "CALENDAR_AUTO_DELETE_ON_CANCEL", True)
    fake_service = MagicMock()
    fake_service.events().delete().execute.return_value = None
    with patch.object(m3, "get_calendar_service", return_value=fake_service):
        result = m3.delete_calendar_event("evt-1")
    assert result is True
    # Delete çağrıldığını doğrula
    fake_service.events().delete.assert_called()
