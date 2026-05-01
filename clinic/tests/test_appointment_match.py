"""
M1 saat dilimi & randevu eşleştirme testleri.

Modül 1 ağır ML bağımlılıkları (faster_whisper, pyannote) içerir.
CI'da bu paketler kurulu olmayabilir; o durumda testler skip edilir.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

m1 = pytest.importorskip(
    "module1_transcription_engine",
    reason="M1 ML bağımlılıkları yüklü değil",
)


IST = ZoneInfo("Europe/Istanbul")


def _appt(event_id: str, start_iso: str) -> dict:
    return {
        "event_id": event_id,
        "summary": "Test Hasta",
        "description": "",
        "start": start_iso,
        "end": start_iso,
        "start_dt": m1._parse_calendar_dt(start_iso),
    }


def test_match_appointments_finds_within_30min_window():
    recorded = datetime(2026, 4, 30, 14, 5, tzinfo=IST)  # 14:05
    appointments = [_appt("e1", "2026-04-30T14:00:00+03:00")]
    matched = m1.match_appointments(recorded, appointments)
    assert len(matched) == 1
    assert matched[0]["event_id"] == "e1"


def test_match_appointments_excludes_far_appointments():
    recorded = datetime(2026, 4, 30, 14, 5, tzinfo=IST)
    appointments = [_appt("e1", "2026-04-30T16:00:00+03:00")]  # ~2 saat sonra
    assert m1.match_appointments(recorded, appointments) == []


def test_match_appointments_handles_naive_recorded_at():
    """Naive datetime → klinik yerel TZ kabul edilmeli."""
    recorded_naive = datetime(2026, 4, 30, 14, 5)
    appointments = [_appt("e1", "2026-04-30T14:00:00+03:00")]
    matched = m1.match_appointments(recorded_naive, appointments)
    assert len(matched) == 1


def test_parse_calendar_dt_naive_dateTime_treated_as_local():
    """Offset'siz dateTime klinik yerel TZ varsayılır."""
    dt = m1._parse_calendar_dt("2026-04-30T14:00:00")
    assert dt.tzinfo is not None
    # IST 14:00 → UTC 11:00
    utc = dt.astimezone(ZoneInfo("UTC"))
    assert utc.hour == 11


def test_parse_calendar_dt_all_day_event():
    """All-day event 'YYYY-MM-DD' formatı."""
    dt = m1._parse_calendar_dt("2026-04-30")
    assert dt.tzinfo is not None
    assert dt.hour == 0


def test_no_match_when_three_hours_off():
    """
    REGRESSION: önceden naive datetime'lar UTC sayılıyordu →
    Türkiye randevusu 3 saat kayıyordu.
    """
    recorded = datetime(2026, 4, 30, 14, 0, tzinfo=IST)  # IST 14:00 = UTC 11:00
    # Eski bug: 14:00 IST recorded (UTC 11:00) ile naive 14:00 UTC sayıldığında
    # 3 saat fark → 30dk window dışı.
    appointments = [_appt("e1", "2026-04-30T14:00:00+03:00")]
    matched = m1.match_appointments(recorded, appointments)
    assert len(matched) == 1, "Aynı IST saati eşleşmeli"
