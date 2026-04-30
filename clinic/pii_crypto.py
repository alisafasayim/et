"""
PII şifreleme/çözme yardımcısı.

KVKK m.6 (özel nitelikli kişisel veri — sağlık verisi) ve m.12
(veri güvenliği) gereği TCKN, telefon, doğum tarihi gibi alanlar
DİSK'TE şifreli saklanır. Anahtar yönetimi:

- PII_ENCRYPTION_KEY env değişkeninde (Fernet base64 key)
- Üretmek için: python -c "from cryptography.fernet import Fernet; \\
                            print(Fernet.generate_key().decode())"
- .env dosyasının izni 600 olmalı (sadece sahibi okur).
- Anahtar kaybolursa şifrelenmiş veri ÇÖZÜLEMEZ — yedek alın.

Hash yardımcıları:
- TCKN gibi alanlarda "aynı TCKN var mı" kontrolü için pseudonymous
  hash kullanılır (deterministik HMAC-SHA256, anahtar PII_HASH_KEY).
  Bu hash hem aramalarda hem de tekilleştirmede kullanılabilir.
"""

import base64
import hashlib
import hmac
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("pii_crypto")

# ---------------------------------------------------------------------------
# Anahtar yönetimi
# ---------------------------------------------------------------------------

_PII_KEY_ENV = "PII_ENCRYPTION_KEY"
_PII_HASH_KEY_ENV = "PII_HASH_KEY"

_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    """Lazy init — testlerde anahtar değiştirilebilsin diye singleton."""
    global _fernet
    if _fernet is None:
        key = os.getenv(_PII_KEY_ENV, "").strip()
        if not key:
            raise EnvironmentError(
                f"{_PII_KEY_ENV} ayarlanmamış. "
                "Üretmek için: python -c "
                "\"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        try:
            _fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as exc:
            raise EnvironmentError(
                f"{_PII_KEY_ENV} geçersiz Fernet anahtarı: {exc}"
            ) from exc
    return _fernet


def reset_cache() -> None:
    """Test'ler için singleton'u sıfırlar."""
    global _fernet
    _fernet = None


# ---------------------------------------------------------------------------
# Şifrele / çöz
# ---------------------------------------------------------------------------

def encrypt(plaintext: str) -> str:
    """
    Düz metni Fernet ile şifreler. Boş/None için boş string döner
    (DB NOT NULL kolonlarında "henüz girilmedi" durumunu temsil etmek
    için sentinel kullanılmaz; doğrudan boş string).
    """
    if not plaintext:
        return ""
    f = _get_fernet()
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(ciphertext: str) -> str:
    """
    Fernet token'ını çözer. Geçersiz/eski anahtarlı token için
    InvalidToken fırlatır — caller karar versin.
    """
    if not ciphertext:
        return ""
    f = _get_fernet()
    try:
        plain = f.decrypt(ciphertext.encode("ascii"))
    except InvalidToken:
        logger.error("PII çözme başarısız (geçersiz token / yanlış anahtar).")
        raise
    return plain.decode("utf-8")


# ---------------------------------------------------------------------------
# Pseudonymous hash — arama / tekilleştirme için
# ---------------------------------------------------------------------------

def pseudo_hash(value: str) -> str:
    """
    Deterministik HMAC-SHA256. PII_HASH_KEY ile salt'lanır.
    Aynı girdi → aynı hash; farklı kurulumlarda farklı (anahtar farklı).
    DB'de "TCKN'ye göre arama" yapmak istiyorsan plaintext yerine bu
    hash'i kıyasla.
    """
    if not value:
        return ""
    key = os.getenv(_PII_HASH_KEY_ENV, "").strip()
    if not key:
        # Hash anahtarı set değilse SHA256 (daha zayıf ama yine pseudonymous)
        # üretim için PII_HASH_KEY mutlaka set edilmeli.
        logger.warning(
            "PII_HASH_KEY set değil; salt'sız SHA256 kullanılıyor (zayıf)."
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    return hmac.new(
        key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


# ---------------------------------------------------------------------------
# Notion / log için anonim görünüm
# ---------------------------------------------------------------------------

def short_pseudonym(uuid_str: str) -> str:
    """
    UUID'yi insanın okuyabileceği kısa bir takma ada çevirir.
    'a4f9c2b1-3e8d-...' → '#a4f9-c2b1' (Notion sayfa başlığında kullanılır)
    """
    if not uuid_str:
        return "#unknown"
    cleaned = uuid_str.replace("-", "").lower()
    if len(cleaned) < 8:
        return f"#{cleaned}"
    return f"#{cleaned[:4]}-{cleaned[4:8]}"
