#!/usr/bin/env python3
"""
Klinik Sistemi — Notion Otomatik Kurulum.

Bir parent page ID alır ve gerekli iki database'i otomatik
oluşturur (Hastalar, İlaçlar). DB ID'lerini stdout'a basar.

ÖNCESİNDE YAPILMASI GEREKEN (kullanıcı tarafı):
  1. notion.so/my-integrations'a git
  2. Yeni internal integration oluştur
     - "Internal" tipini seç (sağlık verisi için)
     - Workspace ki klinik için kullanacağın
  3. Integration secret'ı kopyala (secret_xxx...) → NOTION_TOKEN
  4. Notion'da bir "Klinik" parent page oluştur
  5. Page'in sağ üstünden ⋯ → "Connections" → integration'ı ekle
  6. Page URL'sinden ID'yi al (URL sonu — 32 karakter)
       https://www.notion.so/Klinik-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
                                          ↑ bu ID

Kullanım:
    export NOTION_TOKEN=secret_xxx
    python scripts/setup_notion.py --parent-page <PARENT_PAGE_ID>

    # .env'i otomatik güncelleyerek:
    python scripts/setup_notion.py --parent-page <PAGE_ID> --update-env clinic/.env
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

import requests

NOTION_VERSION = "2022-06-28"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"


def headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Database şemaları
# ---------------------------------------------------------------------------

PATIENTS_SCHEMA = {
    "Hasta Adı": {"title": {}},
    "Randevu Tarihi": {"date": {}},
    "Randevu ID": {"rich_text": {}},
    "Durum": {
        "select": {
            "options": [
                {"name": "Bekliyor", "color": "gray"},
                {"name": "Anamnez Tamamlandı", "color": "blue"},
                {"name": "Arşivlendi", "color": "green"},
                {"name": "Tahsilat Yapıldı", "color": "purple"},
                {"name": "İptal Edildi", "color": "red"},
            ]
        }
    },
    "Veli Telefonu": {"rich_text": {}},
}

MEDICATIONS_SCHEMA = {
    "İlaç": {"title": {}},
    "Hasta": {"rich_text": {}},
    "Doz": {"rich_text": {}},
    "Başlangıç": {"date": {}},
    "Bitiş": {"date": {}},
    "Durum": {
        "select": {
            "options": [
                {"name": "Aktif", "color": "green"},
                {"name": "Tamamlandı", "color": "gray"},
                {"name": "Sonlandırıldı", "color": "red"},
            ]
        }
    },
    "Notlar": {"rich_text": {}},
}


def create_database(token: str, parent_page: str, title: str, schema: dict) -> str:
    """Notion'da yeni database oluşturur, ID'sini döner."""
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page},
        "icon": {"type": "emoji", "emoji": "🏥" if "Hasta" in title else "💊"},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": schema,
    }
    resp = requests.post(
        "https://api.notion.com/v1/databases",
        headers=headers(token),
        json=payload,
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"{RED}✗ {title} oluşturulamadı: {resp.status_code}{RESET}")
        print(f"  {resp.text[:200]}")
        sys.exit(1)
    db_id = resp.json()["id"].replace("-", "")
    print(f"{GREEN}✓{RESET} '{title}' database oluşturuldu: {db_id}")
    return db_id


def update_env(env_file: Path, key: str, value: str) -> None:
    """env dosyasında bir key'i günceller (yoksa ekler)."""
    if not env_file.exists():
        env_file.write_text("")
    lines = env_file.read_text(encoding="utf-8").splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{DIM}  → {env_file} güncellendi: {key}={value[:8]}...{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description=__doc__)
    parser.add_argument("--parent-page", required=True,
                        help="Notion parent page ID (32 karakter, '-' içermez)")
    parser.add_argument("--token", default=os.getenv("NOTION_TOKEN", ""),
                        help="Notion integration token (varsayılan: env)")
    parser.add_argument("--update-env", type=Path, default=None,
                        help="Verilen .env dosyasına ID'leri yaz")
    parser.add_argument("--patients-only", action="store_true",
                        help="Sadece Hastalar DB'sini oluştur")
    args = parser.parse_args()

    if not args.token or args.token.startswith("secret_xxx"):
        print(f"{RED}HATA: NOTION_TOKEN gerekli (export et veya --token ile geç){RESET}")
        return 1

    parent = args.parent_page.replace("-", "")
    if len(parent) != 32:
        print(f"{RED}HATA: parent-page 32 karakter olmalı (verilen: {len(parent)}){RESET}")
        return 1

    print(f"{YELLOW}Notion kurulum başlıyor — parent page: {parent}{RESET}\n")

    # Hastalar DB
    patients_id = create_database(
        args.token, parent, "🏥 Hastalar", PATIENTS_SCHEMA
    )
    if args.update_env:
        update_env(args.update_env, "NOTION_DATABASE_ID", patients_id)

    # İlaçlar DB
    if not args.patients_only:
        meds_id = create_database(
            args.token, parent, "💊 İlaçlar", MEDICATIONS_SCHEMA
        )
        if args.update_env:
            update_env(args.update_env, "NOTION_MEDICATIONS_DATABASE_ID", meds_id)

    print()
    print(f"{GREEN}━━━ Kurulum tamam ━━━{RESET}")
    print()
    print("Şu satırları .env dosyanıza ekleyin (yapmadıysanız):")
    print(f"{DIM}  NOTION_DATABASE_ID={patients_id}{RESET}")
    if not args.patients_only:
        print(f"{DIM}  NOTION_MEDICATIONS_DATABASE_ID={meds_id}{RESET}")
    print()
    print("Test için:")
    print(f"{DIM}  python scripts/test_connections.py --only notion{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
