"""
Yardımcı fonksiyonlar.
"""

import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz
from dateutil import parser as dateparser

logger = logging.getLogger(__name__)

TR_TZ = pytz.timezone("Europe/Istanbul")


def parse_turkish_date(text: str) -> Optional[datetime]:
    """Türkçe tarih ifadelerini parse eder."""
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
      - Kayıt 003.m4a (tarih yok)
    """
    name = Path(filename).stem

    patterns = [
        r"(\d{4})-(\d{2})-(\d{2})",          # 2024-03-15
        r"(\d{2})\.(\d{2})\.(\d{4})",          # 15.03.2024
        r"(\d{4})(\d{2})(\d{2})",              # 20240315
        r"(\d{2})-(\d{2})-(\d{4})",            # 15-03-2024
        r"(\d{2})_(\d{2})_(\d{4})",            # 15_03_2024
    ]

    for i, pattern in enumerate(patterns):
        match = re.search(pattern, name)
        if match:
            groups = match.groups()
            try:
                if i == 0 or i == 2:  # YYYY-MM-DD veya YYYYMMDD
                    return datetime(int(groups[0]), int(groups[1]), int(groups[2]))
                else:  # DD.MM.YYYY vb.
                    return datetime(int(groups[2]), int(groups[1]), int(groups[0]))
            except ValueError:
                continue

    return None


def extract_name_from_filename(filename: str) -> Optional[str]:
    """Ses dosyası adından hasta adını çıkarır."""
    name = Path(filename).stem

    # Tarih ve numaraları kaldır
    cleaned = re.sub(r"\d{4}[-_.]\d{2}[-_.]\d{2}", "", name)
    cleaned = re.sub(r"\d{2}[-_.]\d{2}[-_.]\d{4}", "", cleaned)
    cleaned = re.sub(r"\d{8}", "", cleaned)
    cleaned = re.sub(r"[_\-]+", " ", cleaned).strip()

    # "Kayıt", "session", "part" gibi genel kelimeleri kaldır
    noise_words = {"kayıt", "kayit", "session", "part", "ses", "record", "audio"}
    words = [w for w in cleaned.split() if w.lower() not in noise_words]
    cleaned = " ".join(words).strip()

    # Sadece sayı kaldıysa veya boşsa isim yok
    if not cleaned or cleaned.isdigit():
        return None

    return cleaned


def normalize_turkish_name(name: str) -> str:
    """Türkçe ismi karşılaştırma için normalize eder."""
    replacements = {
        "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
        "Ç": "C", "Ğ": "G", "İ": "I", "Ö": "O", "Ş": "S", "Ü": "U",
    }
    result = name
    for tr_char, en_char in replacements.items():
        result = result.replace(tr_char, en_char)
    return result.lower().strip()


def fuzzy_name_match(name1: str, name2: str, threshold: float = 0.8) -> bool:
    """İki ismin benzerliğini kontrol eder (Türkçe desteği ile)."""
    n1 = normalize_turkish_name(name1)
    n2 = normalize_turkish_name(name2)

    if n1 == n2:
        return True

    # Bir isim diğerinin parçası mı?
    if n1 in n2 or n2 in n1:
        return True

    # Basit karakter benzerliği
    set1, set2 = set(n1.split()), set(n2.split())
    if not set1 or not set2:
        return False
    intersection = set1 & set2
    union = set1 | set2
    jaccard = len(intersection) / len(union)

    return jaccard >= threshold


def time_overlap(start1: datetime, end1: datetime,
                 start2: datetime, end2: datetime) -> bool:
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
