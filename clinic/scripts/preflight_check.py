#!/usr/bin/env python3
"""
Klinik sistemi kurulum doğrulama (preflight check).

Üretime almadan önce çalıştırın. Şunları kontrol eder:
  - Zorunlu env değişkenleri
  - Kritik secret/key uzunlukları
  - Dosya / dizin erişimi (audio_inbox, credentials.json, vs.)
  - Python paketlerinin import edilebilirliği
  - SQLite veritabanlarının yazılabilirliği

Sonuç: çıkış kodu 0 (tüm kontrolller geçti) veya 1 (en az bir hata).

Kullanım:
    python scripts/preflight_check.py
"""

import importlib
import os
import sys
from pathlib import Path

# .env'i bu script çalışırken otomatik yükle
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


class CheckResult:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def ok(self, msg: str) -> None:
        print(f"  {GREEN}✓{RESET} {msg}")

    def warn(self, msg: str) -> None:
        print(f"  {YELLOW}!{RESET} {msg}")
        self.warnings.append(msg)

    def fail(self, msg: str) -> None:
        print(f"  {RED}✗{RESET} {msg}")
        self.errors.append(msg)


def section(title: str) -> None:
    print(f"\n{YELLOW}━━━ {title} ━━━{RESET}")


# ---------------------------------------------------------------------------
# Kontroller
# ---------------------------------------------------------------------------

def check_env_required(r: CheckResult) -> None:
    section("Zorunlu env değişkenleri")
    required = {
        "NOTION_TOKEN": "Notion API integration token (secret_xxx)",
        "NOTION_DATABASE_ID": "Hasta DB'sinin Notion ID'si",
        "EVOLUTION_API_URL": "Evolution API URL",
        "EVOLUTION_API_KEY": "Evolution API key",
        "EVOLUTION_INSTANCE_NAME": "WhatsApp instance adı",
        "DOCTOR_PHONE": "Doktor WhatsApp numarası (risk alarmı için)",
        "GOOGLE_CALENDAR_ID": "Google Calendar ID (varsayılan: primary)",
    }
    for key, desc in required.items():
        val = os.getenv(key, "")
        if not val:
            r.fail(f"{key} eksik — {desc}")
        else:
            r.ok(f"{key} set")


def check_kvkk_keys(r: CheckResult) -> None:
    section("KVKK / PII anahtarları")
    pii_key = os.getenv("PII_ENCRYPTION_KEY", "")
    if not pii_key:
        r.fail("PII_ENCRYPTION_KEY eksik — KVKK hibrit modu çalışmaz")
    else:
        try:
            from cryptography.fernet import Fernet
            Fernet(pii_key.encode())
            r.ok("PII_ENCRYPTION_KEY geçerli Fernet anahtarı")
        except Exception as exc:
            r.fail(f"PII_ENCRYPTION_KEY geçersiz: {exc}")

    pii_hash = os.getenv("PII_HASH_KEY", "")
    if not pii_hash:
        r.fail("PII_HASH_KEY eksik — TCKN/ad araması salt'sız çalışır (zayıf)")
    elif len(pii_hash) < 16:
        r.warn(f"PII_HASH_KEY çok kısa ({len(pii_hash)} char); ≥32 önerilir")
    else:
        r.ok("PII_HASH_KEY uzunluğu yeterli")


def check_webhook_secrets(r: CheckResult) -> None:
    section("Webhook secret'ları")
    require_sig = os.getenv("WEBHOOK_REQUIRE_SIGNATURE", "true").lower() in (
        "1", "true", "yes", "on",
    )
    if require_sig and not os.getenv("WEBHOOK_SECRET"):
        r.fail(
            "WEBHOOK_SECRET eksik (WEBHOOK_REQUIRE_SIGNATURE=true ile fail-closed) — "
            "tüm WhatsApp webhook istekleri reddedilecek"
        )
    elif os.getenv("WEBHOOK_SECRET"):
        r.ok("WEBHOOK_SECRET set")
    else:
        r.warn("WEBHOOK_SECRET yok ve REQUIRE_SIGNATURE=false (DEV ortamı)")

    payment_secret = os.getenv("PAYMENT_WEBHOOK_SECRET", "")
    if payment_secret:
        r.ok("PAYMENT_WEBHOOK_SECRET set")
    else:
        r.warn("PAYMENT_WEBHOOK_SECRET yok — /webhook/payment isteklerini reddedecek")


def check_admin_panel(r: CheckResult) -> None:
    section("Admin paneli")
    if not os.getenv("ADMIN_TOKEN"):
        r.warn("ADMIN_TOKEN yok — /admin/* ve /ui/* endpoint'leri devre dışı (404)")
    else:
        token = os.getenv("ADMIN_TOKEN", "")
        if len(token) < 16:
            r.fail(f"ADMIN_TOKEN çok kısa ({len(token)} char) — brute-force riskli")
        else:
            r.ok("ADMIN_TOKEN set ve yeterli uzunlukta")
    if not os.getenv("FLASK_SECRET_KEY"):
        r.warn(
            "FLASK_SECRET_KEY yok — proses yeniden başlayınca admin UI oturumları geçersiz"
        )
    else:
        r.ok("FLASK_SECRET_KEY set")


def check_taxes(r: CheckResult) -> None:
    section("Vergi oranları (M4 e-SMM)")
    vat = os.getenv("VAT_RATE", "0")
    wh = os.getenv("WITHHOLDING_RATE", "20")
    vat_wh = os.getenv("VAT_WITHHOLDING_RATE", "0")
    r.ok(f"VAT_RATE = %{vat}")
    r.ok(f"WITHHOLDING_RATE = %{wh}")
    r.ok(f"VAT_WITHHOLDING_RATE = %{vat_wh}")
    r.warn(
        "↑ Bu değerleri MALİ MÜŞAVİRİNİZLE TEYİT EDİN. "
        "Yanlış oran vergi eksik kesimine ve idari cezaya yol açabilir."
    )


def check_files(r: CheckResult) -> None:
    section("Dosya / dizin erişimi")
    creds = Path(os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"))
    if creds.exists():
        r.ok(f"Google credentials.json mevcut: {creds}")
    else:
        r.fail(f"Google credentials.json bulunamadı: {creds}")

    audio_inbox = Path(os.getenv("AUDIO_INBOX_DIR", "./audio_inbox"))
    audio_inbox.mkdir(parents=True, exist_ok=True)
    if os.access(audio_inbox, os.W_OK):
        r.ok(f"audio_inbox yazılabilir: {audio_inbox}")
    else:
        r.fail(f"audio_inbox yazılabilir değil: {audio_inbox}")


def check_imports(r: CheckResult) -> None:
    section("Python paket import'ları")
    required = [
        "dotenv",
        "tenacity",
        "cryptography",
        "requests",
        "flask",
        "googleapiclient.discovery",
        "google.oauth2.credentials",
    ]
    optional_ml = [
        ("faster_whisper", "Modül 1 transkripsiyon"),
        ("pyannote.audio", "Modül 1 diarizasyon"),
        ("ollama", "Modül 1 SOAP üretimi"),
        ("docx", "Modül 5 migrasyon"),
    ]
    for mod in required:
        try:
            importlib.import_module(mod)
            r.ok(f"import {mod}")
        except ImportError as exc:
            r.fail(f"{mod} import hatası: {exc}")

    for mod, why in optional_ml:
        try:
            importlib.import_module(mod)
            r.ok(f"{mod} ({why})")
        except ImportError:
            r.warn(f"{mod} yok — {why} çalışmaz; gerekiyorsa kurun")


def check_databases(r: CheckResult) -> None:
    section("SQLite veritabanları")
    paths = {
        "CLINIC_STATE_DB": "./clinic_state.db",
        "PATIENT_REGISTRY_DB": "./patient_registry.db",
        "AUDIT_LOG_DB": "./audit_log.db",
    }
    for env_key, default in paths.items():
        path = Path(os.getenv(env_key, default))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import sqlite3
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE IF NOT EXISTS _preflight (x INTEGER)")
            conn.execute("DROP TABLE _preflight")
            conn.close()
            r.ok(f"{env_key}: {path} yazılabilir")
        except Exception as exc:
            r.fail(f"{env_key}: {path} → {exc}")


def check_https(r: CheckResult) -> None:
    section("HTTPS / public URL")
    public = os.getenv("WEBHOOK_PUBLIC_URL", "")
    if not public:
        r.warn(
            "WEBHOOK_PUBLIC_URL yok — Evolution webhook ve Calendar push çalışmaz"
        )
    elif public.startswith("https://"):
        r.ok(f"WEBHOOK_PUBLIC_URL HTTPS: {public}")
    else:
        r.fail(
            f"WEBHOOK_PUBLIC_URL HTTPS değil ({public}). "
            "Calendar Watch ve güvenli webhook için HTTPS zorunludur."
        )


# ---------------------------------------------------------------------------

def main() -> int:
    r = CheckResult()

    print(f"{YELLOW}Klinik Sistemi — Preflight Check{RESET}")

    check_env_required(r)
    check_kvkk_keys(r)
    check_webhook_secrets(r)
    check_admin_panel(r)
    check_taxes(r)
    check_files(r)
    check_imports(r)
    check_databases(r)
    check_https(r)

    print()
    if r.errors:
        print(f"{RED}━━━ {len(r.errors)} HATA, {len(r.warnings)} uyarı ━━━{RESET}")
        print(f"{RED}Sistem üretime alınamaz. Hataları giderin.{RESET}")
        return 1
    if r.warnings:
        print(f"{YELLOW}━━━ Hata yok, {len(r.warnings)} uyarı ━━━{RESET}")
        print("Uyarıları gözden geçirin; üretim için bazıları gerekli olabilir.")
        return 0
    print(f"{GREEN}━━━ Tüm kontroller başarılı ━━━{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
