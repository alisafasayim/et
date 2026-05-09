"""poll_anamnesis_followup davranışı."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("google.oauth2.credentials", reason="google-auth yüklü değil")


@pytest.fixture
def m3():
    import module3_whatsapp_communicator as mod
    return mod


@pytest.fixture
def fresh_store(tmp_path, monkeypatch):
    """Her test için izole bir state store."""
    import state_store
    store = state_store.StateStore(tmp_path / "test.db")
    monkeypatch.setattr(state_store, "_default_store", store)
    return store


def _appt(event_id: str, phone: str, days_from_now: int = 3) -> dict:
    start = datetime.now(tz=timezone.utc) + timedelta(days=days_from_now)
    return {
        "event_id": event_id,
        "summary": "Hasta Adı",
        "description": f"Tel: {phone}",
        "start": start.isoformat(),
        "start_dt": start,
        "phone": phone,
        "patient_name": "Hasta Adı",
    }


def test_followup_sends_only_when_initial_was_sent(m3, fresh_store, monkeypatch):
    """İlk anamnez mesajı gönderilmemiş randevu için takip atılmamalı."""
    monkeypatch.setenv("GOOGLE_ANAMNESIS_FORM_ID", "form-1")
    appt = _appt("evt-1", "5321234567")

    with patch.object(m3, "fetch_upcoming_appointments", return_value=[appt]), \
         patch.object(m3, "get_calendar_service", return_value=MagicMock()), \
         patch("module2_notion_archiver.get_forms_service", return_value=MagicMock()), \
         patch("module2_notion_archiver.fetch_form_responses", return_value=[]), \
         patch.object(m3, "send_whatsapp_message") as send_mock:
        results = m3.poll_anamnesis_followup()

    send_mock.assert_not_called()
    assert all(r.get("status") != "sent" for r in results)


def test_followup_skips_when_form_response_exists(m3, fresh_store, monkeypatch):
    """Veli formu doldurmuşsa takip mesajı gönderilmemeli."""
    monkeypatch.setenv("GOOGLE_ANAMNESIS_FORM_ID", "form-1")
    appt = _appt("evt-1", "5321234567")
    fresh_store.mark_seen("calendar_event_reminder", "evt-1")  # ilk gönderildi

    fake_response = {"answers": {"Ad Soyad": "Hasta Adı"}}
    with patch.object(m3, "fetch_upcoming_appointments", return_value=[appt]), \
         patch.object(m3, "get_calendar_service", return_value=MagicMock()), \
         patch("module2_notion_archiver.get_forms_service", return_value=MagicMock()), \
         patch("module2_notion_archiver.fetch_form_responses", return_value=[fake_response]), \
         patch("module2_notion_archiver.match_form_response_to_patient", return_value=fake_response), \
         patch.object(m3, "send_whatsapp_message") as send_mock:
        m3.poll_anamnesis_followup()

    send_mock.assert_not_called()


def test_followup_sends_when_initial_done_and_no_form_response(m3, fresh_store, monkeypatch):
    """İlk gönderildi + form boş → takip mesajı gönderilmeli."""
    monkeypatch.setenv("GOOGLE_ANAMNESIS_FORM_ID", "form-1")
    appt = _appt("evt-1", "5321234567")
    fresh_store.mark_seen("calendar_event_reminder", "evt-1")

    with patch.object(m3, "fetch_upcoming_appointments", return_value=[appt]), \
         patch.object(m3, "get_calendar_service", return_value=MagicMock()), \
         patch("module2_notion_archiver.get_forms_service", return_value=MagicMock()), \
         patch("module2_notion_archiver.fetch_form_responses", return_value=[]), \
         patch("module2_notion_archiver.match_form_response_to_patient", return_value=None), \
         patch.object(m3, "send_whatsapp_message") as send_mock:
        results = m3.poll_anamnesis_followup()

    send_mock.assert_called_once()
    assert any(r.get("status") == "sent" for r in results)
    # Idempotent: aynı çağrı tekrar mesaj göndermesin
    with patch.object(m3, "fetch_upcoming_appointments", return_value=[appt]), \
         patch.object(m3, "get_calendar_service", return_value=MagicMock()), \
         patch("module2_notion_archiver.get_forms_service", return_value=MagicMock()), \
         patch("module2_notion_archiver.fetch_form_responses", return_value=[]), \
         patch("module2_notion_archiver.match_form_response_to_patient", return_value=None), \
         patch.object(m3, "send_whatsapp_message") as send_mock_2:
        m3.poll_anamnesis_followup()
    send_mock_2.assert_not_called()


def test_followup_returns_empty_when_no_form_id(m3, fresh_store, monkeypatch):
    monkeypatch.delenv("GOOGLE_ANAMNESIS_FORM_ID", raising=False)
    assert m3.poll_anamnesis_followup() == []
