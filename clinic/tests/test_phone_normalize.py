"""phone_utils.normalize_phone testleri."""

import pytest

from phone_utils import extract_phone_from_description, normalize_phone


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("0532 123 45 67", "905321234567"),
        ("+90 532 123 45 67", "905321234567"),
        ("905321234567", "905321234567"),
        ("5321234567", "905321234567"),  # Ülke kodu yok → 90 ekle
        ("0(532) 123-45-67", "905321234567"),
        ("+90-532-123-4567", "905321234567"),
    ],
)
def test_normalize_phone_returns_digits_only_with_country_code(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "description, expected",
    [
        ("Tel: 05321234567", "05321234567"),
        ("Veli Tel: +90 532 123 4567", "+90 532 123 4567"),
        ("Açıklama yok", ""),
        ("", ""),
    ],
)
def test_extract_phone_from_description(description, expected):
    assert extract_phone_from_description(description) == expected
