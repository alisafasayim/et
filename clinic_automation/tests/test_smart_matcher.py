"""
Akıllı eşleştirme algoritması için birim testleri.
"""

import pytest
from datetime import datetime
from clinic_automation.modules.smart_matcher import SmartMatcher, merge_partial_recordings, MatchResult, PatientSegment
from clinic_automation.modules.google_calendar import Appointment
from clinic_automation.modules.transcription import TranscriptionResult, TranscriptionSegment


def make_appointment(name: str, hour: int = 9, duration: int = 50) -> Appointment:
    start = datetime(2024, 3, 15, hour, 0)
    end = datetime(2024, 3, 15, hour, duration)
    return Appointment(
        patient_name=name,
        start_time=start,
        end_time=end,
        event_id=f"evt_{name}",
    )


def make_transcription(text: str, path: str = "2024-03-15_test.m4a", duration: float = 2400.0) -> TranscriptionResult:
    segments = [TranscriptionSegment(start=0.0, end=duration, text=text)]
    return TranscriptionResult(
        audio_path=path,
        language="tr",
        duration_seconds=duration,
        full_text=text,
        segments=segments,
    )


class TestSmartMatcher:
    def setup_method(self):
        self.matcher = SmartMatcher(name_database=["Ali Veli", "Ayşe Fatma", "Mehmet Kaya"])

    def test_filename_match(self):
        transcription = make_transcription(
            "Bugün görüşmemizde konuştuk.",
            path="2024-03-15_Ali_Veli.m4a",
        )
        appointments = [make_appointment("Ali Veli", 9)]
        result = self.matcher.match_audio(transcription, appointments)
        assert result.patient_segments[0].patient_name == "Ali Veli"
        assert result.patient_segments[0].confidence > 0.3

    def test_content_match(self):
        transcription = make_transcription(
            "Hoş geldiniz Ali Veli, bugün nasılsınız?",
            path="2024-03-15_kayit.m4a",
        )
        appointments = [make_appointment("Ali Veli", 9)]
        result = self.matcher.match_audio(transcription, appointments)
        assert result.patient_segments[0].confidence > 0.0

    def test_no_appointments_uses_filename(self):
        transcription = make_transcription(
            "Görüşme metni.",
            path="2024-03-15_Ayse_Fatma.m4a",
        )
        result = self.matcher.match_audio(transcription, [])
        # Dosya adından tahmin edilmeli
        assert result.patient_segments[0].patient_name is not None

    def test_partial_recording_detected(self):
        transcription = make_transcription(
            "Görüşme devam ediyor.",
            path="2024-03-15_Ali_Veli_part1.m4a",
        )
        appointments = [make_appointment("Ali Veli", 9)]
        result = self.matcher.match_audio(transcription, appointments)
        assert result.is_multi_part is True

    def test_low_confidence_needs_review(self):
        transcription = make_transcription(
            "Bilinmeyen içerik.",
            path="kayit_001.m4a",  # tarih ve isim yok
        )
        result = self.matcher.match_audio(transcription, [])
        assert result.needs_review is True

    def test_multi_source_bonus_applied(self):
        """Dosya adı + DB eşleşmesi -> çoklu kaynak bonusu almalı."""
        transcription = make_transcription(
            "Merhaba Ali Veli, nasılsınız?",
            path="2024-03-15_Ali_Veli.m4a",  # dosya adında da var
        )
        appointments = [make_appointment("Ali Veli", 9)]
        result = self.matcher.match_audio(transcription, appointments)
        # Çoklu kaynak olduğu için güven yüksek olmalı
        assert result.patient_segments[0].confidence > 0.5

    def test_result_has_patient_segment(self):
        transcription = make_transcription("Test metni.", path="2024-03-15_test.m4a")
        result = self.matcher.match_audio(transcription, [])
        assert len(result.patient_segments) >= 1

    def test_result_has_audio_date(self):
        transcription = make_transcription("Test.", path="2024-03-15_test.m4a")
        result = self.matcher.match_audio(transcription, [])
        assert result.audio_date == datetime(2024, 3, 15)


class TestMergePartialRecordings:
    def make_partial_result(self, name: str, path: str) -> MatchResult:
        seg = PatientSegment(
            patient_name=name,
            appointment=None,
            start_time=0.0,
            end_time=600.0,
            transcript_text="Görüşme metni.",
            confidence=0.8,
        )
        return MatchResult(
            audio_path=path,
            audio_date=datetime(2024, 3, 15),
            patient_segments=[seg],
            is_multi_part=True,
        )

    def test_merges_same_patient_parts(self):
        results = [
            self.make_partial_result("Ali Veli", "2024-03-15_Ali_Veli_part1.m4a"),
            self.make_partial_result("Ali Veli", "2024-03-15_Ali_Veli_part2.m4a"),
        ]
        merged = merge_partial_recordings(results)
        ali_results = [r for r in merged if r.patient_segments[0].patient_name == "Ali Veli"]
        assert len(ali_results) == 1

    def test_keeps_different_patients_separate(self):
        results = [
            self.make_partial_result("Ali Veli", "2024-03-15_Ali_part1.m4a"),
            self.make_partial_result("Ayşe Fatma", "2024-03-15_Ayse_part1.m4a"),
        ]
        merged = merge_partial_recordings(results)
        names = {r.patient_segments[0].patient_name for r in merged}
        assert "Ali Veli" in names
        assert "Ayşe Fatma" in names

    def test_non_partial_passed_through(self):
        seg = PatientSegment(
            patient_name="Mehmet",
            appointment=None,
            start_time=0.0,
            end_time=600.0,
            transcript_text="test",
            confidence=0.9,
        )
        non_partial = MatchResult(
            audio_path="2024-03-15_Mehmet.m4a",
            audio_date=datetime(2024, 3, 15),
            patient_segments=[seg],
            is_multi_part=False,
        )
        merged = merge_partial_recordings([non_partial])
        assert len(merged) == 1
        assert merged[0].is_multi_part is False
