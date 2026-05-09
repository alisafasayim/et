"""
Klinik Sistemi — Merkezi Loglama Yapılandırması

İki sorunu birden çözer:
  1. Sınırsız büyüyen clinic.log → RotatingFileHandler (varsayılan
     5 × 10 MB rotation).
  2. Loglara hasta PII'sinin (telefon, TCKN, VKN) düz metin yazılması
     → PIIRedactionFilter regex tabanlı maskeleme.

Kullanım:
    from logging_setup import configure_logging
    configure_logging()  # main.py başında bir kez

    # Modüller her zamanki gibi:
    logger = logging.getLogger("benim_modulum")
    logger.info("Mesaj gönderildi: %s", phone)
    # → "Mesaj gönderildi: 9053*****67" gibi maskelenmiş çıkar
"""

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ---------------------------------------------------------------------------
# PII desenleri (Türkiye)
# ---------------------------------------------------------------------------
# Telefon: 90XXXXXXXXXX, +90 ile veya boşluklu
_PHONE_PATTERNS = [
    re.compile(r"\+?90[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"),
    re.compile(r"\b0\d{3}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b"),
    re.compile(r"\b9?0?5\d{9}\b"),  # 5XXXXXXXXX veya 905XXXXXXXXX
]
# TCKN: 11 hane (1 ile başlar)
_TCKN_PATTERN = re.compile(r"\b[1-9]\d{10}\b")
# VKN: 10 hane
_VKN_PATTERN = re.compile(r"\b\d{10}\b")


def _mask(value: str, keep_left: int = 2, keep_right: int = 2) -> str:
    """Bir string'in baş ve sonu hariç ortasını yıldızla."""
    digits_only = re.sub(r"\D", "", value)
    if len(digits_only) <= keep_left + keep_right:
        return "*" * len(digits_only)
    visible = digits_only[:keep_left] + ("*" * (len(digits_only) - keep_left - keep_right)) + digits_only[-keep_right:]
    return visible


def redact_pii(text: str) -> str:
    """
    Bir string'deki olası TR telefon, TCKN, VKN değerlerini maskeler.
    Sıra önemli: önce daha spesifik (TCKN 11 hane) sonra VKN 10 hane,
    sonra telefon — örtüşmeleri minimize etmek için.
    """
    if not text:
        return text

    text = _TCKN_PATTERN.sub(lambda m: _mask(m.group()), text)
    text = _VKN_PATTERN.sub(lambda m: _mask(m.group()), text)
    for pat in _PHONE_PATTERNS:
        text = pat.sub(lambda m: _mask(m.group()), text)
    return text


class PIIRedactionFilter(logging.Filter):
    """
    Hem record.msg (format string'i) hem de format edilmiş final mesajı
    geçirip PII'yi maskeler. args içindeki string'lere de uygulanır.

    Not: Bu yalnızca bir defansif katmandır; en doğrusu logger'a en
    baştan PII göndermemektir. PII içermesi gereken loglar için
    logger.debug() seviyesine düşürün ve dosya handler'a göndermeyin.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_pii(record.msg)
        if record.args:
            try:
                record.args = tuple(
                    redact_pii(a) if isinstance(a, str) else a for a in record.args
                )
            except Exception:
                # args dict ise veya beklenmedik tip ise loglamayı engelleme
                pass
        return True


# ---------------------------------------------------------------------------
# Yapılandırma
# ---------------------------------------------------------------------------

DEFAULT_LOG_FILE = Path(os.getenv("CLINIC_LOG_FILE", "./clinic.log"))
DEFAULT_LOG_LEVEL = os.getenv("CLINIC_LOG_LEVEL", "INFO").upper()
DEFAULT_LOG_MAX_BYTES = int(os.getenv("CLINIC_LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 MB
DEFAULT_LOG_BACKUP_COUNT = int(os.getenv("CLINIC_LOG_BACKUP_COUNT", "5"))

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


_configured = False


def configure_logging(
    log_file: Path | str | None = None,
    level: str | int | None = None,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> None:
    """
    Root logger'a stream + rotating file handler ekler. PII filter
    her iki handler'a da takılır. Idempotent — birden fazla çağrı
    duplicate handler eklemez.
    """
    global _configured
    if _configured:
        return

    log_file = Path(log_file) if log_file else DEFAULT_LOG_FILE
    level = level or DEFAULT_LOG_LEVEL
    max_bytes = max_bytes or DEFAULT_LOG_MAX_BYTES
    backup_count = backup_count or DEFAULT_LOG_BACKUP_COUNT

    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT)
    pii_filter = PIIRedactionFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(pii_filter)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(pii_filter)

    root = logging.getLogger()
    # Eski handler'ları temizle (basicConfig çağrılmış olabilir)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)
    root.addHandler(stream_handler)
    root.addHandler(file_handler)

    _configured = True
    logging.getLogger("logging_setup").info(
        "Loglama yapılandırıldı | dosya: %s | seviye: %s | rotation: %d × %d MB",
        log_file, level, backup_count, max_bytes // (1024 * 1024),
    )
