#!/usr/bin/env python3
"""
Google OAuth ilk yetkilendirme — token.json üretici.

Module 1/2'den bağımsız (faster-whisper/pyannote import etmez).
Calendar + Forms scope'larıyla OAuth flow başlatır:
  1. Tarayıcı açılır → Gmail hesabıyla giriş
  2. "İzin ver" → token.json yerel olarak yazılır
  3. Sonraki tüm Google API çağrıları bu token'ı kullanır

İKİ HESAP DESTEĞİ
=================
Google Forms ve Google Calendar farklı hesaplarda olabilir.
Her servis için ayrı token dosyası üretmek isterseniz:

    # Forms'un sahibi olan Google hesabıyla giriş
    python scripts/google_auth.py --service forms

    # Calendar'ın sahibi olan diğer hesapla giriş
    python scripts/google_auth.py --service calendar

    # Tek hesap iki servisi de yönetiyorsa (varsayılan):
    python scripts/google_auth.py

`forms` üretirse → token_forms.json
`calendar` üretirse → token_calendar.json
Varsayılanda tek dosya → token.json (geriye uyum)

.env'de GOOGLE_FORMS_TOKEN_FILE ve GOOGLE_CALENDAR_TOKEN_FILE
ile dosya yolları belirtilebilir.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


SCOPES_BY_SERVICE = {
    "calendar": ["https://www.googleapis.com/auth/calendar.readonly"],
    "forms": [
        # Form'un kendisini (soruları, başlığı) okumak için
        "https://www.googleapis.com/auth/forms.body.readonly",
        # Form'a gelen yanıtları (anamnez verisi) okumak için
        "https://www.googleapis.com/auth/forms.responses.readonly",
    ],
    "both": [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/forms.body.readonly",
        "https://www.googleapis.com/auth/forms.responses.readonly",
    ],
}


def _resolve_token_path(service: str) -> str:
    """Servise göre token dosya yolunu env'den oku, yoksa default."""
    if service == "calendar":
        return os.getenv(
            "GOOGLE_CALENDAR_TOKEN_FILE",
            os.getenv("GOOGLE_TOKEN_FILE", "token_calendar.json"),
        )
    if service == "forms":
        return os.getenv(
            "GOOGLE_FORMS_TOKEN_FILE",
            os.getenv("GOOGLE_TOKEN_FILE", "token_forms.json"),
        )
    return os.getenv("GOOGLE_TOKEN_FILE", "token.json")


def main() -> int:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument(
        "--service",
        choices=["calendar", "forms", "both"],
        default="both",
        help=(
            "Hangi servis için token üretilecek. İki ayrı Google hesabı "
            "kullanıyorsanız önce 'forms' sonra 'calendar' ile çalıştırın."
        ),
    )
    args = parser.parse_args()

    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    token_file = _resolve_token_path(args.service)
    scopes = SCOPES_BY_SERVICE[args.service]

    if not Path(creds_file).exists():
        print(f"❌ {creds_file} bulunamadı.")
        print("   GCP Console → OAuth Client ID → JSON indir → clinic/ altına koy")
        return 1

    print(f"🔐 OAuth flow başlatılıyor — servis: {args.service}")
    print(f"   Credentials: {creds_file}")
    print(f"   Token dosyası: {token_file}")
    print(f"   Scopes: {scopes}")
    print()
    if args.service == "calendar":
        print("ℹ️  Google Calendar'ın sahibi olan Gmail hesabıyla giriş yapın.")
    elif args.service == "forms":
        print("ℹ️  Google Forms'un sahibi olan Gmail hesabıyla giriş yapın.")
    else:
        print("ℹ️  Hem Calendar hem Forms sahibi olan tek hesapla giriş yapın.")
    print()

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, scopes)
    creds = flow.run_local_server(port=0, open_browser=True)
    Path(token_file).write_text(creds.to_json(), encoding="utf-8")

    print()
    print(f"✅ Token üretildi: {token_file}")
    print()
    if args.service == "forms":
        print("Şimdi Calendar için:")
        print("    python scripts/google_auth.py --service calendar")
    elif args.service == "calendar":
        print("Şimdi Forms için:")
        print("    python scripts/google_auth.py --service forms")
    print()
    print("Test:")
    print(f"    python scripts/test_connections.py --only {args.service if args.service != 'both' else 'calendar'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
