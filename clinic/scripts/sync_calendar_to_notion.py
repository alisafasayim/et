#!/usr/bin/env python3
"""
Calendar → Notion senkronizasyonu.

Google Calendar'dan yaklaşan randevuları çeker; her hasta için
Notion Hastalar DB'sinde sayfa, Sessions DB'sinde seans satırı
oluşturur. KVKK hibrit modda Notion'a sadece pseudonym yazılır
(gerçek isim yerel patient_registry.db'de Fernet ile şifreli).

Idempotent: aynı Calendar event ID iki kez işlenirse aynı sonucu
verir (state_store 'calendar_sync' namespace'i ile).

Module 1'in ML bağımlılıklarını import etmez — sadece
google-api-python-client + module2 helper'ları.

Kullanım:
    python scripts/sync_calendar_to_notion.py
    python scripts/sync_calendar_to_notion.py --days 14
    python scripts/sync_calendar_to_notion.py --dry-run
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
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

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

logger = logging.getLogger("calendar_sync")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def get_calendar_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_file = os.getenv(
        "GOOGLE_CALENDAR_TOKEN_FILE",
        os.getenv("GOOGLE_TOKEN_FILE", "token.json"),
    )
    if not Path(token_file).exists():
        raise FileNotFoundError(
            f"Calendar token bulunamadı: {token_file}. "
            "Önce: python scripts/google_auth.py --service calendar"
        )
    creds = Credentials.from_authorized_user_file(
        token_file, ["https://www.googleapis.com/auth/calendar.readonly"],
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch_upcoming_events(service, days: int = 7) -> list[dict]:
    cal_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    now = datetime.now(timezone.utc)
    result = service.events().list(
        calendarId=cal_id,
        timeMin=now.isoformat(),
        timeMax=(now + timedelta(days=days)).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=250,
    ).execute()
    return result.get("items", [])


def _extract_patient_name(event: dict) -> str:
    summary = (event.get("summary") or "").strip()
    if not summary:
        return "Bilinmeyen Hasta"
    if ":" in summary:
        parts = summary.split(":", 1)
        if len(parts[1].strip()) > 3:
            summary = parts[1].strip()
    for sep in [" — ", " - ", " | "]:
        if sep in summary:
            summary = summary.split(sep, 1)[0].strip()
    return summary or "Bilinmeyen Hasta"


def _event_start_iso(event: dict) -> str:
    start = event.get("start", {})
    if "dateTime" in start:
        return start["dateTime"][:10]
    if "date" in start:
        return start["date"]
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def sync_event_to_notion(event: dict, dry_run: bool = False) -> dict:
    """
    Tek event'i Notion'a senkronize eder.

    KVKK güvencesi: 'patient_name' alanı her zaman pseudonym (#a4f9-c2b1)
    olarak döner — gerçek isim asla return değerinde veya log'da
    görünmez. Stdout/log'a sadece pseudonym yazılır.
    """
    import json

    event_id = event.get("id", "")
    real_patient_name = _extract_patient_name(event)
    session_date = _event_start_iso(event)

    result = {
        "event_id": event_id,
        "session_date": session_date,
        "status": "pending",
    }

    from state_store import get_default_store
    store = get_default_store()
    if store.is_seen("calendar_sync", event_id):
        result["status"] = "skipped"
        # Skipped'da pseudonym'i state_store meta'sından okuyabilirdik
        # ama log'a basmaya gerek yok. Pseudonym placeholder.
        result["patient_name"] = "(daha önce işlendi)"
        return result

    # Pseudonym her zaman üretilir — dry-run'da bile. Gerçek isim
    # patient_registry.db'de Fernet ile şifreli kalır.
    from module2_notion_archiver import _resolve_patient_root
    from notion_schema import has_separate_sessions_db
    patient_root_id, pseudonym_display = _resolve_patient_root(real_patient_name)
    result["patient_name"] = pseudonym_display

    if dry_run:
        result["status"] = "dry_run"
        return result

    from module2_notion_archiver import create_session_page

    if not has_separate_sessions_db():
        result["status"] = "patient_only"
        result["session_page_id"] = patient_root_id
    else:
        session_page_id = create_session_page(
            patient_page_id=patient_root_id,
            patient_name=pseudonym_display,
            session_date=session_date,
            diagnosis="",
        )
        result["status"] = "synced"
        result["session_page_id"] = session_page_id

    meta_json = json.dumps({
        "session_page_id": result.get("session_page_id", ""),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "patient_name_pseudonym": pseudonym_display,
    })
    store.mark_seen("calendar_sync", event_id, meta=meta_json)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"{YELLOW}Calendar → Notion Senkronizasyonu{RESET}")
    print(f"{DIM}Yaklaşan {args.days} gün, dry_run={args.dry_run}{RESET}\n")

    try:
        service = get_calendar_service()
    except Exception as exc:
        print(f"{RED}✗ Calendar bağlantısı kurulamadı: {exc}{RESET}")
        return 1

    events = fetch_upcoming_events(service, days=args.days)
    if not events:
        print(f"{YELLOW}Yaklaşan {args.days} günde randevu bulunamadı.{RESET}")
        return 0

    print(f"{GREEN}✓{RESET} {len(events)} event bulundu\n")

    summary = {"synced": 0, "skipped": 0, "patient_only": 0, "dry_run": 0, "failed": 0}
    for ev in events:
        try:
            r = sync_event_to_notion(ev, dry_run=args.dry_run)
            status = r["status"]
            summary[status] = summary.get(status, 0) + 1
            icon = {
                "synced": f"{GREEN}✓{RESET}",
                "skipped": f"{DIM}—{RESET}",
                "patient_only": f"{YELLOW}!{RESET}",
                "dry_run": f"{DIM}~{RESET}",
            }.get(status, f"{RED}✗{RESET}")
            print(
                f"  {icon} {r['patient_name']:<30} {r['session_date']} "
                f"{DIM}({status}){RESET}"
            )
        except Exception as exc:
            summary["failed"] += 1
            print(f"  {RED}✗{RESET} Hata: {exc}")
            logger.exception("Sync error for event %s", ev.get("id"))

    print()
    print(f"{YELLOW}━━━ Özet ━━━{RESET}")
    for status, count in summary.items():
        if count > 0:
            print(f"  {status}: {count}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
