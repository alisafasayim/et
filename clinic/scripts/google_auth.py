#!/usr/bin/env python3
"""
Google OAuth ilk yetkilendirme — token.json üretici.

Module 1/2'den bağımsız (faster-whisper/pyannote import etmez).
Calendar + Forms scope'larıyla OAuth flow başlatır:
  1. Tarayıcı açılır → Gmail hesabıyla giriş
  2. "İzin ver" → token.json yerel olarak yazılır
  3. Sonraki tüm Google API çağrıları bu token'ı kullanır

Kullanım:
    python scripts/google_auth.py
"""

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


SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/forms.responses.readonly",
]


def main() -> int:
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    token_file = os.getenv("GOOGLE_TOKEN_FILE", "token.json")

    if not Path(creds_file).exists():
        print(f"❌ {creds_file} bulunamadı.")
        print("   GCP Console → OAuth Client ID → JSON indir → clinic/ altına koy")
        return 1

    print(f"🔐 OAuth flow başlatılıyor...")
    print(f"   Credentials: {creds_file}")
    print(f"   Scopes: {SCOPES}")
    print()
    print("Tarayıcı açılacak. Gmail hesabınla giriş yap ve 'İzin ver' tıkla.")
    print()

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    Path(token_file).write_text(creds.to_json(), encoding="utf-8")

    print()
    print(f"✅ Token üretildi: {token_file}")
    print()
    print("Şimdi test et:")
    print("    python scripts/test_connections.py --only calendar")
    print("    python scripts/test_connections.py --only forms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
