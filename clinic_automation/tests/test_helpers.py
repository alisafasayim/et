"""
Yardımcı fonksiyonlar için birim testleri.
"""

import pytest
from datetime import datetime
from clinic_automation.utils.helpers import (
    extract_date_from_filename,
    extract_name_from_filename,
    normalize_turkish_name,
    fuzzy_name_match,
    format_duration,
    time_overlap,
)


class TestExtractDateFromFilename:
    def test_iso_format(self):
        result = extract_date_from_filename("2024-03-15_Ali_Veli.m4a")
        assert result == datetime(2024, 3, 15)

    def test_dot_format(self):
        result = extract_date_from_filename("15.03.2024 Ayşe Fatma.mp3")
        assert result == datetime(2024, 3, 15)

    def test_compact_format(self):
        result = extract_date_from_filename("20240315_session.m4a")
        assert result == datetime(2024, 3, 15)

    def test_dash_format(self):
        result = extract_date_from_filename("15-03-2024-hasta.m4a")
        assert result == datetime(2024, 3, 15)

    def test_no_date(self):
        result = extract_date_from_filename("Kayıt 003.m4a")
        assert result is None

    def test_numbered_only(self):
        result = extract_date_from_filename("ses_kaydı_001.mp3")
        assert result is None


class TestExtractNameFromFilename:
    def test_name_after_date(self):
        result = extract_name_from_filename("2024-03-15_Ali_Veli.m4a")
        assert result is not None
        assert "Ali" in result or "Veli" in result

    def test_no_name_numbered(self):
        result = extract_name_from_filename("Kayıt 003.m4a")
        assert result is None

    def test_no_name_session(self):
        result = extract_name_from_filename("session_001.m4a")
        assert result is None

    def test_name_with_spaces(self):
        result = extract_name_from_filename("15.03.2024 Ahmet Mehmet.mp3")
        assert result is not None


class TestNormalizeTurkishName:
    def test_lowercase(self):
        assert normalize_turkish_name("ALİ") == "ali"

    def test_turkish_chars(self):
        result = normalize_turkish_name("Çiğdem Şahin")
        assert "c" in result
        assert "s" in result
        assert "ğ" not in result

    def test_already_normalized(self):
        result = normalize_turkish_name("ali veli")
        assert result == "ali veli"

    def test_all_turkish_chars(self):
        result = normalize_turkish_name("ÇĞİÖŞÜçğışöşü")
        assert "Ç" not in result
        assert "Ğ" not in result
        assert "İ" not in result


class TestFuzzyNameMatch:
    def test_exact_match(self):
        assert fuzzy_name_match("Ali Veli", "Ali Veli") is True

    def test_case_insensitive(self):
        assert fuzzy_name_match("ali veli", "ALİ VELİ") is True

    def test_turkish_chars(self):
        assert fuzzy_name_match("Ayşe Şahin", "ayse sahin") is True

    def test_partial_match(self):
        assert fuzzy_name_match("Ali", "Ali Veli") is True

    def test_no_match(self):
        assert fuzzy_name_match("Ahmet Kaya", "Zeynep Demir") is False

    def test_empty_strings(self):
        assert fuzzy_name_match("", "Ali") is False


class TestFormatDuration:
    def test_seconds_only(self):
        assert "dk" in format_duration(90)
        assert "sn" in format_duration(90)

    def test_hours(self):
        result = format_duration(3700)
        assert "sa" in result

    def test_zero(self):
        result = format_duration(0)
        assert result is not None


class TestTimeOverlap:
    def test_overlapping(self):
        s1 = datetime(2024, 1, 1, 9, 0)
        e1 = datetime(2024, 1, 1, 10, 0)
        s2 = datetime(2024, 1, 1, 9, 30)
        e2 = datetime(2024, 1, 1, 10, 30)
        assert time_overlap(s1, e1, s2, e2) is True

    def test_non_overlapping(self):
        s1 = datetime(2024, 1, 1, 9, 0)
        e1 = datetime(2024, 1, 1, 10, 0)
        s2 = datetime(2024, 1, 1, 10, 0)
        e2 = datetime(2024, 1, 1, 11, 0)
        assert time_overlap(s1, e1, s2, e2) is False

    def test_contained(self):
        s1 = datetime(2024, 1, 1, 9, 0)
        e1 = datetime(2024, 1, 1, 11, 0)
        s2 = datetime(2024, 1, 1, 9, 30)
        e2 = datetime(2024, 1, 1, 10, 30)
        assert time_overlap(s1, e1, s2, e2) is True
