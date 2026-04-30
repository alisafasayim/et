"""
Telefon numarası yardımcıları.

M3 (WhatsApp) ve M4 (e-SMM PDF iletim) ortak olarak kullanır.
Ağır bağımlılıklar (Flask, Google-auth) içermez — testlerde kolayca
import edilebilir.
"""

import re


def normalize_phone(phone: str) -> str:
    """
    Telefon numarasını Evolution API'nin beklediği formata getirir.
    Sadece rakamları tutar; başındaki 0 atılıp 90 ile değiştirilir;
    ülke kodu yoksa 90 önek olarak eklenir.

    Örn:
        "0532 123 45 67"   → "905321234567"
        "+90 532 123 4567" → "905321234567"
        "5321234567"       → "905321234567"
    """
    digits = "".join(filter(str.isdigit, phone))
    if digits.startswith("0"):
        digits = "90" + digits[1:]
    elif not digits.startswith("90"):
        digits = "90" + digits
    return digits


_PHONE_FROM_DESCRIPTION = re.compile(
    r"(?:veli\s*)?tel[:\s]*([0-9\s\+\-\(\)]{10,20})",
    re.IGNORECASE,
)


def extract_phone_from_description(description: str) -> str:
    """
    Calendar etkinliği açıklamasından telefonu regex ile ayıklar.
    Beklenen format örnekleri:
        "Tel: 05321234567"
        "Veli Tel: +90 532 123 4567"
    """
    if not description:
        return ""
    match = _PHONE_FROM_DESCRIPTION.search(description)
    return match.group(1).strip() if match else ""
