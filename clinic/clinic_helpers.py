"""
Yardımcı fonksiyonlar (clinic/ flat yapısı için port).

clinic_automation/utils/helpers.py'den port. Smart matcher tarafından
kullanılır. Türkçe karakter normalizasyonu, dosya adından tarih/isim
çıkarma, fuzzy isim eşleştirme ve zaman çakışma kontrolü.

Ek olarak smart_matcher'ın bağımlılığı olan minimum dataclass'lar:
  - Appointment (Calendar event)
  - TranscriptionSegment (Whisper segment)
Bu dataclass'lar clinic/'in dict-tabanlı yapısıyla `from_dict` ile
köprülenir — module1 dict döndürür, smart_matcher dataclass beklemiyor.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Minimal dataclass'lar (clinic_automation google_calendar/transcription'dan)
# ---------------------------------------------------------------------------

@dataclass
class Appointment:
    """Google Calendar randevu (clinic_automation Appointment uyumlu)."""
    patient_name: str
    start_time: datetime
    end_time: datetime
    summary: str = ""
    event_id: str = ""
    description: str = ""

    @property
    def duration_minutes(self) -> float:
        """Randevu süresi (dakika)."""
        delta = self.end_time - self.start_time
        return delta.total_seconds() / 60.0

    @classmethod
    def from_dict(cls, data: dict) -> "Appointment":
        """clinic Modül 1'in dict çıktısını Appointment'a dönüştürür."""
        return cls(
            patient_name=data.get("patient_name") or data.get("summary", ""),
            start_time=_to_datetime(data.get("start_time") or data.get("start")),
            end_time=_to_datetime(data.get("end_time") or data.get("end")),
            summary=data.get("summary", ""),
            event_id=data.get("event_id") or data.get("id", ""),
            description=data.get("description", ""),
        )


@dataclass
class TranscriptionSegment:
    """Whisper transkripsiyon segmenti (clinic_automation uyumlu)."""
    start: float  # saniye
    end: float    # saniye
    text: str
    speaker: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "TranscriptionSegment":
        return cls(
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            text=data.get("text", "").strip(),
            speaker=data.get("speaker", ""),
        )


@dataclass
class TranscriptionResult:
    """Bütün dosyanın transkripsiyonu (clinic_automation uyumlu)."""
    audio_path: str = ""
    language: str = "tr"
    duration_seconds: float = 0.0
    full_text: str = ""
    segments: list[TranscriptionSegment] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    # Eski clinic adlandırması ile geriye uyum
    @property
    def file_path(self) -> str:
        return self.audio_path

    @property
    def duration(self) -> float:
        return self.duration_seconds


def _to_datetime(value) -> datetime:
    """ISO string, datetime veya None'ı datetime'a çevirir."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now()
    return datetime.now()


# ---------------------------------------------------------------------------
# Helpers (clinic_automation/utils/helpers.py'den port)
# ---------------------------------------------------------------------------

def parse_turkish_date(text: str) -> Optional[datetime]:
    """Türkçe tarih ifadelerini parse eder."""
    try:
        from dateutil import parser as dateparser
    except ImportError:
        logger.warning("python-dateutil yüklü değil; parse_turkish_date no-op")
        return None

    turkish_months = {
        "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4,
        "mayıs": 5, "haziran": 6, "temmuz": 7, "ağustos": 8,
        "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
    }
    text_lower = text.lower().strip()

    for month_name, month_num in turkish_months.items():
        if month_name in text_lower:
            text_lower = text_lower.replace(month_name, str(month_num))
            break

    try:
        return dateparser.parse(text_lower, dayfirst=True)
    except (ValueError, TypeError):
        return None


def extract_date_from_filename(filename: str) -> Optional[datetime]:
    """Ses dosyası adından tarih çıkarır.

    Desteklenen formatlar:
      - 2024-03-15_Ali_Veli.m4a
      - 15.03.2024 Ali Veli.m4a
      - 20240315_session.m4a
      - özge eylül aydoğan 16.04.m4a (DD.MM → mevcut yıl)
      - Kayıt 003.m4a (tarih yok → None)
    """
    name = Path(filename).stem

    patterns = [
        r"(\d{4})-(\d{2})-(\d{2})",
        r"(\d{2})\.(\d{2})\.(\d{4})",
        r"(\d{4})(\d{2})(\d{2})",
        r"(\d{2})-(\d{2})-(\d{4})",
        r"(\d{2})_(\d{2})_(\d{4})",
    ]

    for i, pattern in enumerate(patterns):
        match = re.search(pattern, name)
        if match:
            groups = match.groups()
            try:
                if i in (0, 2):  # YYYY-MM-DD veya YYYYMMDD
                    return datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                return datetime(int(groups[2]), int(groups[1]), int(groups[0]))
            except ValueError:
                continue

    short_match = re.search(r"(?<!\d)(\d{2})\.(\d{2})(?!\.\d)", name)
    if short_match:
        day, month = int(short_match.group(1)), int(short_match.group(2))
        try:
            return datetime(datetime.now().year, month, day)
        except ValueError:
            pass

    return None


def extract_name_from_filename(filename: str) -> Optional[str]:
    """Ses dosyası adından hasta adını çıkarır."""
    name = Path(filename).stem

    cleaned = re.sub(r"\d{4}[-_.]\d{2}[-_.]\d{2}", "", name)
    cleaned = re.sub(r"\d{2}[-_.]\d{2}[-_.]\d{4}", "", cleaned)
    cleaned = re.sub(r"\d{8}", "", cleaned)
    cleaned = re.sub(r"(?<!\d)\d{2}\.\d{2}(?!\.\d)", "", cleaned)
    cleaned = re.sub(r"[_\-]+", " ", cleaned).strip()

    noise_words = {
        "kayıt", "kayit", "session", "part", "ses", "record", "audio",
        "processed", "seans",
    }
    words = [w for w in cleaned.split() if w.lower() not in noise_words]
    cleaned = " ".join(words).strip()

    if not cleaned or cleaned.isdigit():
        return None
    return cleaned


def normalize_turkish_name(name: str) -> str:
    """Türkçe ismi karşılaştırma için normalize eder."""
    replacements = {
        "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
        "Ç": "C", "Ğ": "G", "İ": "I", "Ö": "O", "Ş": "S", "Ü": "U",
    }
    result = name or ""
    for tr_char, en_char in replacements.items():
        result = result.replace(tr_char, en_char)
    return result.lower().strip()


def fuzzy_name_match(name1: str, name2: str, threshold: float = 0.8) -> bool:
    """İki ismin benzerliğini kontrol eder (Türkçe desteği ile)."""
    n1 = normalize_turkish_name(name1)
    n2 = normalize_turkish_name(name2)

    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    if n1 in n2 or n2 in n1:
        return True

    set1, set2 = set(n1.split()), set(n2.split())
    if not set1 or not set2:
        return False
    jaccard = len(set1 & set2) / len(set1 | set2)
    return jaccard >= threshold


def time_overlap(
    start1: datetime, end1: datetime, start2: datetime, end2: datetime
) -> bool:
    """İki zaman aralığının çakışıp çakışmadığını kontrol eder."""
    return start1 < end2 and start2 < end1


def format_duration(seconds: float) -> str:
    """Saniyeyi okunabilir formata çevirir."""
    td = timedelta(seconds=int(seconds))
    hours, remainder = divmod(td.seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}sa {minutes}dk {secs}sn"
    return f"{minutes}dk {secs}sn"
