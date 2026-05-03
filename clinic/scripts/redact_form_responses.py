#!/usr/bin/env python3
"""
KVKK Acil Düzeltme — Form Response sayfalarını Notion'dan arşivle.

Önceki create_form_response_page() davranışı: form yanıtlarındaki
soru-cevap çiftlerini Notion sayfasına block olarak yazıyordu.
Bu PII'yi (TC, ad, telefon, anne-baba ad/meslek, okul, ev adresi)
yurtdışı sunucuya (Notion=ABD-host) **ham olarak** taşıdı.

KVKK m.6 (özel nitelikli sağlık verisi) + m.9 (yurtdışı transfer)
ihlali. Bu script o sayfaları toplu arşivler:
- PATCH /pages/{id} archived=true
- Sayfalar 30 gün trash'ta tutulur, sonra Notion otomatik kalıcı siler
- Audit log'a 'kvkk_redaction' event'i yazılır (m.12)
- state_store 'form_sync' namespace'i temizlenir (yeniden temiz sync için)

Kullanım:
    python scripts/redact_form_responses.py            # dry-run varsayılan
    python scripts/redact_form_responses.py --apply    # gerçek arşivleme
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
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

import requests

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"


def fetch_anamnez_pages(token: str, db_id: str) -> list[dict]:
    """Form Responses DB'sindeki 'Anamnez' başlıklı tüm sayfaları çek."""
    from notion_schema import form_response_props
    fr = form_response_props()

    pages = []
    cursor = None
    while True:
        payload = {
            "filter": {
                "property": fr.title,
                "title": {"starts_with": "Anamnez"},
            },
            "page_size": 100,
        }
        if cursor:
            payload["start_cursor"] = cursor

        r = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return pages


def archive_page(token: str, page_id: str) -> bool:
    """Tek bir sayfayı arşivle (PATCH archived=true)."""
    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={"archived": True},
        timeout=30,
    )
    return r.status_code in (200, 201)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Gerçek arşivleme yap (varsayılan dry-run)")
    args = parser.parse_args()

    token = os.getenv("NOTION_TOKEN")
    db_id = os.getenv("NOTION_FORM_RESPONSES_DB_ID")
    if not token or not db_id:
        print(f"{RED}✗ NOTION_TOKEN veya NOTION_FORM_RESPONSES_DB_ID eksik{RESET}")
        return 1

    print(f"{YELLOW}KVKK Acil Düzeltme — Form Response Arşivleme{RESET}")
    print(f"{DIM}Mod: {'APPLY (gerçek arşivleme)' if args.apply else 'dry-run'}{RESET}\n")

    print(f"{DIM}Anamnez sayfaları çekiliyor...{RESET}")
    pages = fetch_anamnez_pages(token, db_id)
    print(f"{GREEN}✓{RESET} {len(pages)} Anamnez sayfası bulundu\n")

    if not pages:
        print(f"{YELLOW}Arşivlenecek sayfa yok.{RESET}")
        return 0

    if not args.apply:
        print(f"{YELLOW}DRY-RUN: --apply ile çalıştırırsanız {len(pages)} sayfa arşivlenir.{RESET}")
        print(f"{DIM}Tahmini süre: ~{len(pages) * 0.4 / 60:.1f} dk (3 req/sec rate limit){RESET}\n")
        print(f"İlk 3 sayfa örneği:")
        for p in pages[:3]:
            from notion_schema import form_response_props
            t = p["properties"].get(form_response_props().title, {}).get("title", [])
            title = t[0].get("plain_text", "?") if t else "?"
            print(f"  - {title}  ({p['id']})")
        return 0

    # Audit log başlat
    started_at = datetime.now(timezone.utc).isoformat()

    archived = failed = 0
    for i, p in enumerate(pages, 1):
        page_id = p["id"]
        try:
            ok = archive_page(token, page_id)
            if ok:
                archived += 1
                if i % 50 == 0:
                    print(f"{DIM}  {i}/{len(pages)} arşivlendi...{RESET}")
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            print(f"{RED}  ✗ {page_id[:8]}: {exc}{RESET}")
        time.sleep(0.4)  # Notion rate limit'e saygı

    finished_at = datetime.now(timezone.utc).isoformat()

    # Audit log + state_store reset
    try:
        from state_store import get_default_store
        store = get_default_store()

        # state_store form_sync namespace'i sıfırla — yeniden temiz sync için
        with store._cursor() as cur:
            cur.execute("DELETE FROM processed WHERE namespace='form_sync'")
            removed = cur.rowcount

        # KVKK m.12 audit log
        with store._cursor() as cur:
            cur.execute(
                "INSERT INTO processed (namespace, key, meta) VALUES (?, ?, ?)",
                (
                    "kvkk_audit",
                    f"redaction_{started_at[:19]}",
                    json.dumps({
                        "event": "form_response_pii_redaction",
                        "reason": "KVKK m.6 + m.9 ihlali — PII (TC/ad/telefon vs.) "
                                  "Notion sayfa block'larında yurtdışı sunucuda kalmış",
                        "action": "Notion sayfaları arşivlendi (PATCH archived=true)",
                        "pages_archived": archived,
                        "pages_failed": failed,
                        "form_sync_state_removed": removed,
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "operator": os.getenv("USER", os.getenv("USERNAME", "unknown")),
                    }),
                ),
            )
        print(f"\n{GREEN}✓ Audit log yazıldı (kvkk_audit namespace){RESET}")
        print(f"{GREEN}✓ state_store form_sync namespace temizlendi ({removed} kayıt){RESET}")
    except Exception as exc:
        print(f"{YELLOW}⚠️ Audit log/state reset hatası: {exc}{RESET}")

    print(f"\n{YELLOW}━━━ Özet ━━━{RESET}")
    print(f"  arşivlendi: {archived}")
    print(f"  başarısız: {failed}")
    print(f"  state_store temizlendi (form_sync)")
    print(f"\n{DIM}Sayfalar 30 gün Notion trash'ta. Sonra otomatik kalıcı silinir.{RESET}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
