#!/usr/bin/env python3
"""
Klinik Sistemi — Dış Servis Bağlantı Testi.

Her servise read-only ping atar; hasta verisi yazmaz.
Hangileri çalışıyor / hangileri eksik raporlar.

Servisler:
  - Notion    (users.me)
  - Google Calendar (calendars.get primary)
  - Google Forms    (forms.get FORM_ID)
  - Evolution API   (instance/connectionState)
  - Paraşüt v4      (OAuth token + me/companies)
  - Ollama (local)  (list models)

Kullanım:
    python scripts/test_connections.py
    python scripts/test_connections.py --only notion
"""

import argparse
import os
import sys
from pathlib import Path

# Windows'ta unicode çıktı için stdout'u UTF-8'e çevir (cp1254 ile çakışmasın)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# .env'i otomatik yükle
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"


def header(name: str, info: str = "") -> None:
    print(f"\n{YELLOW}━━━ {name} ━━━{RESET}")
    if info:
        print(f"{DIM}{info}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def skip(msg: str) -> None:
    print(f"  {DIM}—{RESET} {DIM}{msg}{RESET}")


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------

def test_notion() -> bool:
    header("Notion", "users.me ping")
    token = os.getenv("NOTION_TOKEN", "")
    if not token or token.startswith("secret_xxx"):
        skip("NOTION_TOKEN boş veya placeholder; atlanıyor")
        return True  # not failure, just skipped
    try:
        import requests
        resp = requests.get(
            "https://api.notion.com/v1/users/me",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            ok(f"Bağlandı: {data.get('name', '?')} ({data.get('type', '?')})")
            db_id = os.getenv("NOTION_DATABASE_ID", "")
            if db_id and not db_id.startswith("xxxx"):
                # DB schema kontrolü
                r2 = requests.get(
                    f"https://api.notion.com/v1/databases/{db_id}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Notion-Version": "2022-06-28",
                    },
                    timeout=10,
                )
                if r2.status_code == 200:
                    props = r2.json().get("properties", {})
                    ok(f"Hasta DB erişilebilir: {len(props)} property")
                    needed = {"Hasta Adı", "Randevu Tarihi", "Randevu ID", "Durum"}
                    missing = needed - set(props.keys())
                    if missing:
                        fail(f"Eksik property: {', '.join(missing)}")
                        return False
                    else:
                        ok("Tüm gerekli property'ler mevcut")
                else:
                    fail(f"DB erişimi başarısız: {r2.status_code} {r2.text[:80]}")
                    return False
            else:
                skip("NOTION_DATABASE_ID boş; DB schema kontrolü atlandı")
            return True
        else:
            fail(f"HTTP {resp.status_code}: {resp.text[:100]}")
            return False
    except Exception as exc:
        fail(f"Hata: {exc}")
        return False


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------

def _google_creds():
    """OAuth credentials.json'dan service oluştur (token.json varsa kullan)."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    token_file = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
    scopes = [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/forms.responses.readonly",
    ]

    if not Path(creds_file).exists():
        return None, f"credentials.json bulunamadı: {creds_file}"

    creds = None
    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            return None, (
                f"token.json yok veya geçersiz. "
                f"İlk yetkilendirme için: python -c 'from module1_transcription_engine "
                f"import get_calendar_service; get_calendar_service()'"
            )
        Path(token_file).write_text(creds.to_json())
    return creds, None


def test_calendar() -> bool:
    header("Google Calendar", "calendars.get primary")
    creds, err = _google_creds()
    if err:
        skip(err)
        return True
    try:
        from googleapiclient.discovery import build
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        cal_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
        result = service.calendars().get(calendarId=cal_id).execute()
        ok(f"Calendar: {result.get('summary', '?')} ({result.get('timeZone', '?')})")
        # Yaklaşan event sayısı
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        events = service.events().list(
            calendarId=cal_id,
            timeMin=now_iso,
            maxResults=5,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        ok(f"Yaklaşan {len(events.get('items', []))} event okunabildi")
        return True
    except Exception as exc:
        fail(f"Hata: {exc}")
        return False


# ---------------------------------------------------------------------------
# Google Forms
# ---------------------------------------------------------------------------

def test_forms() -> bool:
    header("Google Forms", "forms.get FORM_ID")
    form_id = os.getenv("GOOGLE_ANAMNESIS_FORM_ID", "")
    if not form_id or form_id.startswith("1FAIpQLSxxx"):
        skip("GOOGLE_ANAMNESIS_FORM_ID boş; atlanıyor")
        return True
    creds, err = _google_creds()
    if err:
        skip(err)
        return True
    try:
        from googleapiclient.discovery import build
        service = build("forms", "v1", credentials=creds, cache_discovery=False)
        form = service.forms().get(formId=form_id).execute()
        title = form.get("info", {}).get("title", "?")
        items = form.get("items", [])
        ok(f"Form: '{title}' ({len(items)} soru)")
        # responses sayısı
        try:
            resp = service.forms().responses().list(formId=form_id).execute()
            count = len(resp.get("responses", []))
            ok(f"{count} cevap kaydı erişilebilir")
        except Exception as exc:
            fail(f"Cevaplar okunamadı: {exc}")
            return False
        return True
    except Exception as exc:
        fail(f"Hata: {exc}")
        return False


# ---------------------------------------------------------------------------
# Evolution API
# ---------------------------------------------------------------------------

def test_evolution() -> bool:
    header("Evolution API (WhatsApp)", "instance/connectionState")
    url = os.getenv("EVOLUTION_API_URL", "")
    key = os.getenv("EVOLUTION_API_KEY", "")
    instance = os.getenv("EVOLUTION_INSTANCE_NAME", "")
    if not (url and key and instance):
        skip("EVOLUTION_API_URL / API_KEY / INSTANCE_NAME eksik")
        return True
    try:
        import requests
        resp = requests.get(
            f"{url.rstrip('/')}/instance/connectionState/{instance}",
            headers={"apikey": key},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            state = data.get("instance", {}).get("state", "?")
            if state == "open":
                ok(f"WhatsApp instance bağlı: {instance}")
            else:
                fail(f"Instance state: {state} (panelden QR taratın)")
                return False
            return True
        else:
            fail(f"HTTP {resp.status_code}: {resp.text[:100]}")
            return False
    except Exception as exc:
        fail(f"Hata: {exc}")
        return False


# ---------------------------------------------------------------------------
# Paraşüt
# ---------------------------------------------------------------------------

def test_parasut() -> bool:
    header("Paraşüt v4", "OAuth token + me/companies")
    if not os.getenv("PARASUT_CLIENT_ID"):
        skip("PARASUT_* env'leri boş")
        return True
    try:
        import requests
        resp = requests.post(
            "https://api.parasut.com/oauth/token",
            data={
                "grant_type": "password",
                "client_id": os.getenv("PARASUT_CLIENT_ID"),
                "client_secret": os.getenv("PARASUT_CLIENT_SECRET"),
                "username": os.getenv("PARASUT_USERNAME"),
                "password": os.getenv("PARASUT_PASSWORD"),
            },
            timeout=15,
        )
        if resp.status_code != 200:
            fail(f"Token alınamadı: HTTP {resp.status_code} {resp.text[:100]}")
            return False
        token = resp.json()["access_token"]
        ok("OAuth token alındı")

        company_id = os.getenv("PARASUT_COMPANY_ID", "")
        if not company_id:
            skip("PARASUT_COMPANY_ID boş")
            return True
        r2 = requests.get(
            f"https://api.parasut.com/v4/{company_id}/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if r2.status_code == 200:
            data = r2.json().get("data", {}).get("attributes", {})
            ok(f"Şirket erişildi: {data.get('name', '?')}")
            return True
        else:
            fail(f"Şirket erişilemedi: HTTP {r2.status_code}")
            return False
    except Exception as exc:
        fail(f"Hata: {exc}")
        return False


# ---------------------------------------------------------------------------
# Ollama (local)
# ---------------------------------------------------------------------------

def test_ollama() -> bool:
    header("Ollama (yerel LLM)", "list models")
    try:
        import ollama
    except ImportError:
        skip("ollama paketi kurulu değil — ML deps")
        return True
    try:
        models = ollama.list().get("models", [])
        if not models:
            fail("Hiç model yok. `ollama pull llama3` çalıştırın")
            return False
        wanted = os.getenv("OLLAMA_MODEL", "llama3")
        names = [m.get("name", m.get("model", "")) for m in models]
        if any(n.startswith(wanted) for n in names):
            ok(f"Model mevcut: {wanted}")
        else:
            fail(f"OLLAMA_MODEL='{wanted}' indirilmemiş. Mevcut: {names}")
            return False
        return True
    except Exception as exc:
        fail(f"Hata (Ollama daemon çalışıyor mu?): {exc}")
        return False


# ---------------------------------------------------------------------------

TESTS = {
    "notion": test_notion,
    "calendar": test_calendar,
    "forms": test_forms,
    "evolution": test_evolution,
    "parasut": test_parasut,
    "ollama": test_ollama,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=list(TESTS.keys()),
                        help="Sadece bir servisi test et")
    args = parser.parse_args()

    print(f"{YELLOW}Klinik Sistemi — Bağlantı Testi{RESET}")

    targets = [args.only] if args.only else list(TESTS.keys())
    failures = 0
    for name in targets:
        if not TESTS[name]():
            failures += 1

    print()
    if failures:
        print(f"{RED}━━━ {failures} servisten hata aldı ━━━{RESET}")
        print("env değişkenlerini ve KURULUM.md'yi kontrol edin.")
        return 1
    print(f"{GREEN}━━━ Tüm aktif servisler bağlandı ━━━{RESET}")
    print(f"{DIM}(Boş env'ler atlandı — KURULUM.md'ye göre doldurun){RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
