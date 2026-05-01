#!/usr/bin/env python3
"""
Klinik sistemi için tüm gerekli secret/key değerlerini üreten yardımcı.

Üretilen değerler stdout'a .env formatında basılır. Çıktıyı .env
dosyanıza yapıştırın veya `>> .env` ile append edin.

ÖNEMLİ:
  - PII_ENCRYPTION_KEY kaybolursa şifreli hasta verisi ÇÖZÜLEMEZ.
    Anahtarı güvenli bir yerde yedekleyin (parola yöneticisi vb.).
  - .env dosyasının izni 600 olmalı: chmod 600 .env
  - Bu script'i sadece İLK kurulumda çalıştırın; yeniden çalıştırmak
    yeni anahtarlar üretir ve mevcut şifreli veriler kullanılamaz hale
    gelir.

Kullanım:
    python scripts/generate_secrets.py
    python scripts/generate_secrets.py --append clinic/.env
"""

import argparse
import secrets
import sys
from pathlib import Path


def gen_fernet_key() -> str:
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        sys.stderr.write(
            "HATA: cryptography paketi yüklü değil. "
            "Önce 'pip install cryptography' yapın.\n"
        )
        sys.exit(1)
    return Fernet.generate_key().decode("ascii")


def gen_random(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def gen_hex(nbytes: int = 32) -> str:
    return secrets.token_hex(nbytes)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Klinik için secret/key üretici",
    )
    parser.add_argument(
        "--append",
        type=Path,
        help=(
            "Çıktıyı verilen .env dosyasının sonuna ekle "
            "(varsa yedek alın!)"
        ),
    )
    args = parser.parse_args()

    lines = [
        "# ===== Üretilmiş secret/key değerleri =====",
        "# generate_secrets.py tarafından üretildi.",
        "# UYARI: PII_ENCRYPTION_KEY kaybolursa hasta verisi çözülemez.",
        "",
        "# --- KVKK / PII şifreleme ---",
        f"PII_ENCRYPTION_KEY={gen_fernet_key()}",
        f"PII_HASH_KEY={gen_random(32)}",
        "",
        "# --- Webhook secret'lar ---",
        f"WEBHOOK_SECRET={gen_random(32)}",
        f"PAYMENT_WEBHOOK_SECRET={gen_random(32)}",
        "",
        "# --- Calendar push (opsiyonel) ---",
        f"CALENDAR_PUSH_TOKEN={gen_random(32)}",
        "",
        "# --- Admin paneli ---",
        f"ADMIN_TOKEN={gen_random(32)}",
        f"FLASK_SECRET_KEY={gen_hex(32)}",
        "",
        "# ===== Üretim sonu =====",
    ]
    output = "\n".join(lines) + "\n"

    if args.append:
        if not args.append.exists():
            sys.stderr.write(f"UYARI: {args.append} mevcut değil; yine de oluşturuluyor.\n")
        with args.append.open("a", encoding="utf-8") as f:
            f.write("\n" + output)
        sys.stderr.write(f"✓ Secret'lar eklendi: {args.append}\n")
        sys.stderr.write("  chmod 600 ile dosya izinlerini sıkılaştırın:\n")
        sys.stderr.write(f"    chmod 600 {args.append}\n")
    else:
        sys.stdout.write(output)
        sys.stderr.write("\n→ Çıktıyı .env dosyanıza yapıştırın veya --append kullanın.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
