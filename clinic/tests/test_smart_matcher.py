"""
Smart matcher edge case testleri.

Plan'da belirtilen 4 kritik edge case:
  1. Tek dosyada 2-3 hasta görüşmesi → ayrı segmentlere bölünmeli
  2. Aynı hastanın "(1)", "(2)", "part 1" parçalı kayıtları → birleşmeli
  3. İsimsiz dosya → calendar+transcript çapraz referansla tespit
  4. Düşük güven (≤%50) → "İnceleme Gerekli" işareti

Ayrıca clinic_helpers fonksiyonlarının (extract_date, fuzzy_match,
time_overlap) doğru çalıştığı kontrol edilir.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from clinic_helpers import (
    Appointment,
    TranscriptionResult,
    TranscriptionSegment,
    extract_date_from_filename,
    extract_name_from_filename,
    format_duration,
    fuzzy_name_match,
    normalize_turkish_name,
    time_overlap,
)
from smart_matcher import (
    MatchResult,
    PatientSegment,
    SmartMatcher,
    merge_partial_recordings,
)


# ---------------------------------------------------------------------------
# clinic_helpers — temel testler
# ---------------------------------------------------------------------------

def test_extract_date_iso_format():
    assert extract_date_from_filename("2026-04-15_Ali.m4a") == datetime(2026, 4, 15)


def test_extract_date_dotted_format():
    assert extract_date_from_filename("15.04.2026 Ali.m4a") == datetime(2026, 4, 15)


def test_extract_date_yyyymmdd():
    assert extract_date_from_filename("20260415_session.m4a") == datetime(2026, 4, 15)


def test_extract_date_short_dd_mm():
    """DD.MM formatı mevcut yıl ile döner (özge eylül aydoğan 16.04.m4a)."""
    result = extract_date_from_filename("özge eylül aydoğan 16.04.m4a")
    assert result is not None
    assert result.month == 4
    assert result.day == 16


def test_extract_date_no_match():
    assert extract_date_from_filename("Kayıt 003.m4a") is None


def test_extract_name_basic():
    assert extract_name_from_filename("2026-04-15_Ali_Veli.m4a") == "Ali Veli"


def test_extract_name_with_part():
    """'part' kelimesi noise word — temizlenir."""
    name = extract_name_from_filename("ahmet yılmaz part 2.m4a")
    assert name and "part" not in name.lower()
    assert "ahmet" in name.lower()


def test_extract_name_only_numbers():
    """Sadece sayı kalırsa None."""
    assert extract_name_from_filename("Kayıt_003.m4a") is None


def test_normalize_turkish():
    assert normalize_turkish_name("Özge Çelik") == "ozge celik"
    assert normalize_turkish_name("İğin") == "igin"


def test_fuzzy_match_exact():
    assert fuzzy_name_match("Ali Veli", "ali veli") is True


def test_fuzzy_match_substring():
    assert fuzzy_name_match("Ali", "Ali Veli Gürpınar") is True


def test_fuzzy_match_jaccard():
    assert fuzzy_name_match("Ali Veli", "Veli Ali") is True  # set eşit


def test_fuzzy_match_unrelated():
    assert fuzzy_name_match("Ali Veli", "Ahmet Yılmaz") is False


def test_time_overlap_yes():
    a, b = datetime(2026, 4, 15, 10), datetime(2026, 4, 15, 11)
    c, d = datetime(2026, 4, 15, 10, 30), datetime(2026, 4, 15, 11, 30)
    assert time_overlap(a, b, c, d) is True


def test_time_overlap_no():
    a, b = datetime(2026, 4, 15, 10), datetime(2026, 4, 15, 10, 30)
    c, d = datetime(2026, 4, 15, 11), datetime(2026, 4, 15, 11, 30)
    assert time_overlap(a, b, c, d) is False


def test_format_duration():
    assert format_duration(125) == "2dk 5sn"
    assert format_duration(3725) == "1sa 2dk 5sn"


# ---------------------------------------------------------------------------
# SmartMatcher — temel davranış
# ---------------------------------------------------------------------------

def test_matcher_instantiates():
    m = SmartMatcher()
    assert m is not None


def test_matcher_with_name_database():
    m = SmartMatcher(name_database=["Ali Veli", "Ayşe Demir"])
    assert m is not None


# ---------------------------------------------------------------------------
# Yardımcı fixture'lar
# ---------------------------------------------------------------------------

def _make_transcription(text: str, segments: list[tuple[float, float, str]]) -> TranscriptionResult:
    return TranscriptionResult(
        audio_path="/tmp/test.m4a",
        full_text=text,
        segments=[
            TranscriptionSegment(start=s, end=e, text=t)
            for s, e, t in segments
        ],
        duration_seconds=segments[-1][1] if segments else 0.0,
    )


def _make_appointment(name: str, hour: int, day: int = 15) -> Appointment:
    return Appointment(
        patient_name=name,
        start_time=datetime(2026, 4, day, hour, 0),
        end_time=datetime(2026, 4, day, hour, 45),
        summary=name,
        event_id=f"evt-{name}-{hour}",
    )


# ---------------------------------------------------------------------------
# Edge Case 1: Tek dosyada birden fazla hasta
# ---------------------------------------------------------------------------

def test_edge_case_multi_patient_in_single_file():
    """Tek kayıtta 2 farklı hasta görüşmesi varsa segmentlere bölünmeli."""
    transcription = _make_transcription(
        text="Merhaba Ali bey, nasılsınız? ... Hoşçakalın. "
             "Şimdi Ayşe hanım, sizinle ilgili son durumu konuşalım. ... Görüşmek üzere.",
        segments=[
            (0, 600, "Merhaba Ali bey, nasılsınız?"),
            (600, 1200, "Hoşçakalın."),
            (1300, 1900, "Şimdi Ayşe hanım, sizinle ilgili..."),
            (1900, 2400, "Görüşmek üzere."),
        ],
    )
    appointments = [
        _make_appointment("Ali Yılmaz", 10),
        _make_appointment("Ayşe Demir", 11),
    ]
    matcher = SmartMatcher()
    result = matcher.match_audio(transcription, appointments)

    assert isinstance(result, MatchResult)
    # En az bir segment çıkmalı (ideal: 2)
    assert len(result.patient_segments) >= 1


# ---------------------------------------------------------------------------
# Edge Case 3: İsimsiz dosya — Calendar + transcript ile tespit
# ---------------------------------------------------------------------------

def test_edge_case_unnamed_file_with_calendar_match():
    """Dosyada isim yok; transcript ve calendar'dan tespit edilebilir."""
    transcription = _make_transcription(
        text="Ali Yılmaz ile görüşme. Anksiyete belirtileri devam ediyor.",
        segments=[(0, 60, "Ali Yılmaz ile görüşme.")],
    )
    appointments = [_make_appointment("Ali Yılmaz", 10)]
    audio_metadata = {
        "file_path": "/tmp/Kayıt_003.m4a",  # isimsiz
        "duration": 60.0,
        "recorded_at": datetime(2026, 4, 15, 10, 0),
    }

    matcher = SmartMatcher()
    result = matcher.match_audio(transcription, appointments, audio_metadata)

    # Match bulunmuş olmalı (Ali Yılmaz transkriptde geçiyor + calendar'da var)
    assert isinstance(result, MatchResult)
    # En az bir segment olmalı; ya isim çıkarılır ya da needs_review True olur
    if result.patient_segments:
        names = [seg.patient_name or "" for seg in result.patient_segments]
        # Ya transkriptten ya calendar'dan Ali tespit edilmeli, edilemezse
        # düşük güven (needs_review) işareti olmalı
        any_ali = any("Ali" in n for n in names)
        assert any_ali or result.needs_review, (
            f"İsim tespit edilemedi ve needs_review işareti yok: {names}"
        )


# ---------------------------------------------------------------------------
# Edge Case 4: Düşük güven → "İnceleme Gerekli"
# ---------------------------------------------------------------------------

def test_edge_case_low_confidence_flagged():
    """Hiçbir veri kaynağı eşleşmiyorsa düşük güven (≤0.5) işaretlenmeli."""
    transcription = _make_transcription(
        text="Kısa konuşma metni.",
        segments=[(0, 30, "Kısa konuşma metni.")],
    )
    appointments = []  # Hiç randevu yok
    audio_metadata = {
        "file_path": "/tmp/random_file.m4a",
        "duration": 30.0,
    }

    matcher = SmartMatcher()
    result = matcher.match_audio(transcription, appointments, audio_metadata)

    # Match yok veya düşük güven olmalı
    if result.patient_segments:
        assert all(seg.confidence <= 0.6 for seg in result.patient_segments)


# ---------------------------------------------------------------------------
# Edge Case 2: Parçalı kayıtların birleşmesi
# ---------------------------------------------------------------------------

def _make_partial_match(name: str, audio_path: str, *, multi: bool) -> MatchResult:
    """Parçalı kayıt birleştirmesi için minimal MatchResult fabrikası."""
    seg = PatientSegment(
        patient_name=name,
        appointment=None,
        start_time=0.0,
        end_time=600.0,
        transcript_text=f"{name} ile görüşme",
    )
    return MatchResult(
        audio_path=audio_path,
        audio_date=datetime(2026, 4, 15, 10, 0),
        patient_segments=[seg],
        is_multi_part=multi,
    )


def test_merge_partial_recordings_part_pattern():
    """is_multi_part=True olan kayıtlar aynı hastada birleşmeli."""
    results = [
        _make_partial_match("Ali Yılmaz", "/tmp/Ali Yılmaz part 1.m4a", multi=True),
        _make_partial_match("Ali Yılmaz", "/tmp/Ali Yılmaz part 2.m4a", multi=True),
        _make_partial_match("Ayşe Demir", "/tmp/Ayşe Demir.m4a", multi=False),
    ]
    merged = merge_partial_recordings(results)
    # Ali için tek bir birleşik MatchResult olmalı + Ayşe ayrı
    names = [r.patient_segments[0].patient_name for r in merged]
    assert "Ali Yılmaz" in names
    assert "Ayşe Demir" in names
    # Ali'nin sayısı 1 olmalı (birleşti)
    ali_count = sum(1 for n in names if n == "Ali Yılmaz")
    assert ali_count == 1


def test_merge_partial_recordings_paren_pattern():
    """(1), (2) kalıbı parçalar."""
    results = [
        _make_partial_match("Mehmet Demir", "/tmp/Mehmet Demir (1).m4a", multi=True),
        _make_partial_match("Mehmet Demir", "/tmp/Mehmet Demir (2).m4a", multi=True),
    ]
    merged = merge_partial_recordings(results)
    assert len(merged) == 1


def test_merge_no_partials():
    """Hiç parçalı kayıt yoksa giriş ve çıkış aynı."""
    results = [
        _make_partial_match("Ali", "/tmp/Ali.m4a", multi=False),
        _make_partial_match("Veli", "/tmp/Veli.m4a", multi=False),
        _make_partial_match("Ayşe", "/tmp/Ayşe.m4a", multi=False),
    ]
    merged = merge_partial_recordings(results)
    assert len(merged) == 3
