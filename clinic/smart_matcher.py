"""
Akıllı Eşleştirme Algoritması
==============================
Ses kayıtlarını doğru hastalarla eşleştirir.

Üç veri kaynağını çapraz referanslar:
1. Dosya metadata'sı (isim, tarih, süre)
2. Google Calendar randevu blokları
3. Transkript içeriği (isim geçişleri, bağlam değişiklikleri)

Edge Case'ler:
- İsimsiz/sadece tarihli dosyalar
- Tek dosyada birden fazla hastanın görüşmesi
- Aynı hastanın parçalı (part) kayıtları
"""

import logging
import re
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from clinic_helpers import Appointment
from clinic_helpers import TranscriptionResult, TranscriptionSegment
from clinic_helpers import (
    extract_date_from_filename,
    extract_name_from_filename,
    fuzzy_name_match,
    time_overlap,
    format_duration,
)

logger = logging.getLogger(__name__)


@dataclass
class PatientSegment:
    """Bir ses kaydının belirli bir hastaya ait bölümü."""
    patient_name: str
    appointment: Optional[Appointment]
    start_time: float  # ses dosyasındaki başlangıç (saniye)
    end_time: float    # ses dosyasındaki bitiş (saniye)
    transcript_text: str
    segments: list[TranscriptionSegment] = field(default_factory=list)
    confidence: float = 0.0  # 0.0 - 1.0 eşleştirme güveni
    match_reasons: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    """Bir ses dosyasının tam eşleştirme sonucu."""
    audio_path: str
    audio_date: Optional[datetime]
    patient_segments: list[PatientSegment]
    original_audio_path: str = ""
    processed_audio_path: str = ""
    is_multi_patient: bool = False
    is_multi_part: bool = False
    needs_review: bool = False
    review_reason: str = ""


class SmartMatcher:
    """Ses kaydı - hasta eşleştirme motoru."""

    # Görüşme başlangıç/bitiş ipuçları
    SESSION_START_PATTERNS = [
        r"hoş\s*geldin",
        r"merhaba.*nasılsın",
        r"buyurun?\s*oturun",
        r"geçen\s*haftadan\s*beri",
        r"son\s*görüşmemizden",
        r"bugün\s*sizi.*getir",
    ]

    SESSION_END_PATTERNS = [
        r"görüşmek\s*üzere",
        r"haftaya\s*görüşürüz",
        r"iyi\s*günler",
        r"randevumuzu.*ayarlayalım",
        r"kontrol.*gelin",
        r"tedaviye.*devam",
    ]

    # İsim geçişi ipuçları
    NAME_INTRODUCTION_PATTERNS = [
        r"ben\s+(\w+\s+\w+)",
        r"adım\s+(\w+)",
        r"(\w+)\s+hanım.*hoş\s*geldin",
        r"(\w+)\s+bey.*hoş\s*geldin",
        r"(\w+)'[ıiuü]n\s+annesi",
        r"(\w+)'[ıiuü]n\s+babası",
    ]

    def __init__(self, name_database: list[str] | None = None):
        """
        Args:
            name_database: Bilinen hasta isimlerinin listesi (Notion'dan çekilir).
        """
        self.known_patients = name_database or []

    def match_audio(
        self,
        transcription: TranscriptionResult,
        appointments: list[Appointment],
        audio_metadata: dict | None = None,
    ) -> MatchResult:
        """Ana eşleştirme fonksiyonu.

        Adımlar:
        1. Dosya adından tarih ve isim çıkar
        2. Calendar'dan aday randevuları belirle
        3. Transkriptte isim ve bağlam değişikliği ara
        4. Skorla ve en iyi eşleşmeyi seç
        """
        audio_path = transcription.audio_path
        filename = Path(audio_path).name

        # 1. Dosya metadata'sından bilgi çıkar
        file_date = extract_date_from_filename(filename)
        file_name = extract_name_from_filename(filename)
        file_duration = transcription.duration_seconds

        if audio_metadata and not file_date:
            file_date = audio_metadata.get("created_date")

        logger.info(
            "Eşleştirme başlıyor: %s (tarih: %s, isim: %s, süre: %s)",
            filename,
            file_date.strftime("%Y-%m-%d") if file_date else "?",
            file_name or "?",
            format_duration(file_duration),
        )

        # 2. O güne ait randevuları filtrele
        day_appointments = self._filter_appointments_by_date(appointments, file_date)

        # 3. Transkriptte bağlam değişikliği (çoklu hasta) kontrol et
        split_points = self._detect_session_boundaries(transcription)

        if split_points:
            # Çoklu hasta durumu
            patient_segments = self._match_multi_patient(
                transcription, day_appointments, split_points, file_name
            )
            needs_review = any(s.confidence < self.CONFIDENCE_AUTO_ACCEPT for s in patient_segments)
            return MatchResult(
                audio_path=audio_path,
                audio_date=file_date,
                patient_segments=patient_segments,
                is_multi_patient=True,
                needs_review=needs_review,
                review_reason="Çoklu hasta kaydı tespit edildi, doğrulama önerilir." if needs_review else "",
            )

        # 4. Tek hasta eşleştirmesi
        segment = self._match_single_patient(
            transcription, day_appointments, file_name, file_date, file_duration
        )

        # 5. Parçalı kayıt kontrolü
        is_part = self._detect_partial_recording(filename)

        # 6. Güven seviyesine göre inceleme kararı
        if segment.confidence >= self.CONFIDENCE_AUTO_ACCEPT:
            needs_review = False
            review_reason = ""
        elif segment.confidence >= self.CONFIDENCE_REVIEW:
            needs_review = True
            review_reason = f"Güven skoru {segment.confidence:.0%} < %{self.CONFIDENCE_AUTO_ACCEPT:.0%}, manuel doğrulama önerilir."
        else:
            needs_review = True
            review_reason = f"Düşük güven skoru ({segment.confidence:.0%}), eşleştirme reddedildi. Manuel inceleme kuyruğuna alındı."

        return MatchResult(
            audio_path=audio_path,
            audio_date=file_date,
            patient_segments=[segment],
            is_multi_patient=False,
            is_multi_part=is_part,
            needs_review=needs_review,
            review_reason=review_reason,
        )

    def _filter_appointments_by_date(
        self,
        appointments: list[Appointment],
        target_date: Optional[datetime],
    ) -> list[Appointment]:
        """Hedef tarihteki randevuları filtreler."""
        if not target_date:
            return appointments

        return [
            a for a in appointments
            if a.start_time.date() == target_date.date()
            and a.status != "cancelled"
        ]

    def _detect_session_boundaries(
        self,
        transcription: TranscriptionResult,
    ) -> list[float]:
        """Transkriptte görüşme geçiş noktalarını tespit eder.

        Returns:
            Bölünme noktalarının zaman listesi (saniye).
        """
        split_points = []
        segments = transcription.segments

        if not segments:
            return []

        for i, seg in enumerate(segments):
            text_lower = seg.text.lower()

            # Bitiş + Başlangıç pattern'i ara
            is_ending = any(re.search(p, text_lower) for p in self.SESSION_END_PATTERNS)

            if is_ending and i + 1 < len(segments):
                next_text = segments[i + 1].text.lower()
                is_starting = any(re.search(p, next_text) for p in self.SESSION_START_PATTERNS)

                # Arada sessizlik var mı?
                gap = segments[i + 1].start - seg.end
                has_gap = gap > 10  # 10 saniyeden uzun sessizlik

                if is_starting or has_gap:
                    split_points.append(seg.end)
                    logger.info(
                        "Görüşme geçişi tespit edildi: %.0f sn (boşluk: %.0f sn)",
                        seg.end, gap,
                    )

            # Farklı bir isimle tanışma tespit et
            for pattern in self.NAME_INTRODUCTION_PATTERNS:
                match = re.search(pattern, text_lower)
                if match and i > len(segments) * 0.1:  # İlk %10'u atla
                    # Bu isim bilinen bir hasta mı?
                    found_name = match.group(1)
                    if any(fuzzy_name_match(found_name, known) for known in self.known_patients):
                        if seg.start not in split_points:
                            # Önceki segmentte sessizlik var mı kontrol et
                            if i > 0:
                                gap = seg.start - segments[i - 1].end
                                if gap > 5:
                                    split_points.append(segments[i - 1].end)

        return sorted(set(split_points))

    def _match_multi_patient(
        self,
        transcription: TranscriptionResult,
        appointments: list[Appointment],
        split_points: list[float],
        file_name: Optional[str],
    ) -> list[PatientSegment]:
        """Çoklu hasta kaydını bölüp her parçayı eşleştirir."""
        boundaries = [0.0] + split_points + [transcription.duration_seconds]
        patient_segments = []

        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]

            # Bu aralıktaki segmentleri topla
            part_segments = [
                s for s in transcription.segments
                if s.start >= start and s.end <= end
            ]
            part_text = " ".join(s.text for s in part_segments)

            # Bu parça için en iyi randevuyu bul
            best_match = self._score_appointments(
                appointments, part_text, file_name, start, end, transcription
            )

            if best_match:
                appt, score, reasons = best_match
                patient_segments.append(PatientSegment(
                    patient_name=appt.patient_name,
                    appointment=appt,
                    start_time=start,
                    end_time=end,
                    transcript_text=part_text,
                    segments=part_segments,
                    confidence=score,
                    match_reasons=reasons,
                ))
            else:
                patient_segments.append(PatientSegment(
                    patient_name="Bilinmeyen Hasta",
                    appointment=None,
                    start_time=start,
                    end_time=end,
                    transcript_text=part_text,
                    segments=part_segments,
                    confidence=0.0,
                    match_reasons=["Eşleşme bulunamadı"],
                ))

        return patient_segments

    def _match_single_patient(
        self,
        transcription: TranscriptionResult,
        appointments: list[Appointment],
        file_name: Optional[str],
        file_date: Optional[datetime],
        file_duration: float,
    ) -> PatientSegment:
        """Tek hastayı eşleştirir."""
        best = self._score_appointments(
            appointments,
            transcription.full_text,
            file_name,
            0,
            file_duration,
            transcription,
        )

        if best:
            appt, score, reasons = best
            return PatientSegment(
                patient_name=appt.patient_name,
                appointment=appt,
                start_time=0,
                end_time=file_duration,
                transcript_text=transcription.full_text,
                segments=transcription.segments,
                confidence=score,
                match_reasons=reasons,
            )

        # Dosya adındaki ismi son çare olarak kullan
        return PatientSegment(
            patient_name=file_name or "Bilinmeyen Hasta",
            appointment=None,
            start_time=0,
            end_time=file_duration,
            transcript_text=transcription.full_text,
            segments=transcription.segments,
            confidence=0.3 if file_name else 0.0,
            match_reasons=["Sadece dosya adından tahmin"] if file_name else ["Eşleşme bulunamadı"],
        )

    # Çoklu kaynak eşleşme bonusu: aynı hasta ID birden fazla kaynakta -> +0.15
    MULTI_SOURCE_BONUS = 0.15

    # Manuel inceleme eşikleri (ses_kayit_sistemi_tasarimi.md)
    CONFIDENCE_AUTO_ACCEPT = 0.70   # Otomatik kabul
    CONFIDENCE_REVIEW = 0.50        # Manuel inceleme
    CONFIDENCE_REJECT = 0.30        # Reddedilir, kuyruğa alınır
    TRANSCRIPTION_QUALITY_MIN = 0.6 # Minimum transkripsiyon kalitesi
    DATE_MISMATCH_MAX_DAYS = 7      # Tarih uyuşmazlığı toleransı

    def _score_appointments(
        self,
        appointments: list[Appointment],
        text: str,
        file_name: Optional[str],
        segment_start: float,
        segment_end: float,
        transcription: TranscriptionResult,
    ) -> Optional[tuple[Appointment, float, list[str]]]:
        """Her randevuyu skorlar ve en iyi eşleşmeyi döndürür.

        Skorlama kriterleri (toplam 1.0 + bonus):
        - Dosya adı isim eşleşmesi:    0.30
        - Calendar zaman eşleşmesi:     0.25
        - Transkript içerik eşleşmesi:  0.25
        - Veritabanı (Notion) eşleşmesi: 0.10
        - Süre uyumu:                   0.10
        - Çoklu kaynak bonusu:          +0.15
        """
        if not appointments:
            return None

        scores = []

        for appt in appointments:
            score = 0.0
            reasons = []
            source_matches = 0  # Kaç kaynaktan eşleşme var

            # 1. Dosya adı isim eşleşmesi (0.30)
            if file_name and fuzzy_name_match(file_name, appt.patient_name):
                score += 0.30
                source_matches += 1
                reasons.append(f"Dosya adı eşleşmesi: '{file_name}' ≈ '{appt.patient_name}'")

            # 2. Calendar zaman eşleşmesi (0.25)
            duration_seconds = segment_end - segment_start
            if duration_seconds > 0:
                audio_date = extract_date_from_filename(Path(transcription.audio_path).name)
                if audio_date:
                    appt_order = appointments.index(appt)
                    total_appts = len(appointments)
                    relative_position = segment_start / max(transcription.duration_seconds, 1)

                    if total_appts > 0:
                        expected_position = appt_order / total_appts
                        position_diff = abs(relative_position - expected_position)
                        time_score = max(0, 0.25 * (1 - position_diff * 2))
                        score += time_score
                        if time_score > 0.12:
                            source_matches += 1
                            reasons.append(f"Zaman sırası uyumlu (sıra: {appt_order + 1}/{total_appts})")

            # 3. Transkript içerik eşleşmesi (0.25)
            content_score = self._score_content_match(text, appt.patient_name)
            score += content_score * 0.25
            if content_score > 0.5:
                source_matches += 1
                reasons.append("Transkriptte isim/bağlam eşleşmesi")

            # 4. Veritabanı (bilinen hasta) eşleşmesi (0.10)
            if any(fuzzy_name_match(appt.patient_name, known) for known in self.known_patients):
                score += 0.10
                source_matches += 1
                reasons.append("Notion veritabanında kayıtlı hasta")

            # 5. Süre uyumu (0.10)
            segment_duration_min = (segment_end - segment_start) / 60
            appt_duration_min = appt.duration_minutes
            if appt_duration_min > 0:
                duration_ratio = min(segment_duration_min, appt_duration_min) / max(
                    segment_duration_min, appt_duration_min
                )
                duration_score = duration_ratio * 0.10
                score += duration_score
                if duration_ratio > 0.5:
                    reasons.append(f"Süre uyumu: {segment_duration_min:.0f}dk ≈ {appt_duration_min}dk")

            # 6. Çoklu kaynak bonusu (+0.15)
            if source_matches >= 2:
                score += self.MULTI_SOURCE_BONUS
                reasons.append(f"Çoklu kaynak bonusu: {source_matches} kaynak eşleşti (+{self.MULTI_SOURCE_BONUS})")

            scores.append((appt, score, reasons))

        if not scores:
            return None

        # En yüksek skoru seç
        scores.sort(key=lambda x: x[1], reverse=True)
        best = scores[0]

        if best[1] > 0.2:  # Minimum eşik
            return best
        return None

    def _score_content_match(self, text: str, patient_name: str) -> float:
        """Transkript içeriğinin hasta ile ne kadar eşleştiğini skorlar."""
        text_lower = text.lower()
        name_parts = patient_name.lower().split()

        score = 0.0

        # İsim geçiyor mu?
        for part in name_parts:
            if len(part) > 2 and part in text_lower:
                score += 0.4
                break

        # Tanışma kalıpları
        for pattern in self.NAME_INTRODUCTION_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                found = match.group(1)
                if fuzzy_name_match(found, patient_name):
                    score += 0.6
                    break

        return min(score, 1.0)

    @staticmethod
    def _detect_partial_recording(filename: str) -> bool:
        """Dosya adının parçalı kayıt olup olmadığını kontrol eder."""
        name_lower = filename.lower()
        partial_patterns = [
            r"part\s*\d+",
            r"parça\s*\d+",
            r"bölüm\s*\d+",
            r"\(\d+\)",
            r"_\d+\s*\.",
            r"-\s*\d+\s*\.",
        ]
        return any(re.search(p, name_lower) for p in partial_patterns)


def merge_partial_recordings(
    match_results: list[MatchResult],
) -> list[MatchResult]:
    """Aynı hastanın parçalı kayıtlarını birleştirir."""
    # Hasta adına göre grupla
    patient_groups: dict[str, list[MatchResult]] = {}
    non_partial = []

    for result in match_results:
        if result.is_multi_part and result.patient_segments:
            name = result.patient_segments[0].patient_name
            patient_groups.setdefault(name, []).append(result)
        else:
            non_partial.append(result)

    merged = list(non_partial)

    for patient_name, parts in patient_groups.items():
        if len(parts) <= 1:
            merged.extend(parts)
            continue

        # Tarih ve zamana göre sırala
        parts.sort(key=lambda r: r.audio_date or datetime.min)

        # Segmentleri birleştir
        combined_text = []
        combined_segments = []
        total_duration = 0.0

        for part in parts:
            for seg in part.patient_segments:
                adjusted_seg = PatientSegment(
                    patient_name=seg.patient_name,
                    appointment=seg.appointment,
                    start_time=seg.start_time + total_duration,
                    end_time=seg.end_time + total_duration,
                    transcript_text=seg.transcript_text,
                    segments=seg.segments,
                    confidence=seg.confidence,
                    match_reasons=seg.match_reasons,
                )
                combined_segments.append(adjusted_seg)
                combined_text.append(seg.transcript_text)
                total_duration += seg.end_time - seg.start_time

        merged_result = MatchResult(
            audio_path=parts[0].audio_path,
            audio_date=parts[0].audio_date,
            patient_segments=[PatientSegment(
                patient_name=patient_name,
                appointment=parts[0].patient_segments[0].appointment if parts[0].patient_segments else None,
                start_time=0,
                end_time=total_duration,
                transcript_text=" ".join(combined_text),
                segments=[s for seg in combined_segments for s in seg.segments],
                confidence=max(s.confidence for s in combined_segments),
                match_reasons=[f"Birleştirilmiş kayıt ({len(parts)} parça)"],
            )],
            original_audio_path=parts[0].original_audio_path or parts[0].audio_path,
            processed_audio_path=parts[0].processed_audio_path,
            is_multi_patient=False,
            is_multi_part=True,
        )
        merged.append(merged_result)

        logger.info(
            "%s için %d parçalı kayıt birleştirildi (toplam: %s).",
            patient_name, len(parts), format_duration(total_duration),
        )

    return merged
