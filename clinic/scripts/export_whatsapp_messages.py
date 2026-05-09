#!/usr/bin/env python3
"""
WhatsApp tüm mesajları JSONL export + PII redaksiyon.

WAHA'dan tüm chat'lerin tüm mesajlarını çeker, KVKK uyumlu redaksiyon
uygular, JSONL olarak yerel diske yazar.

Bu dataset RAG (vektör DB + LLM) için kullanılacak. Her satır bir
mesaj objesi:

{
  "chat_pseudonym": "#a4f9-c2b1",      // patient_registry'den
  "chat_id_hash": "1e9a3f2b",           // chat_id'nin SHA256 ilk 8 hane
  "msg_id": "ABCD1234...",
  "fromMe": false,                       // true = doktor, false = hasta
  "timestamp": 1704654321,               // unix sn
  "body": "balık yağı kullanmaya başlayalım mı?",  // PII redacted
  "type": "text" | "image" | ...,
}

PII redaksiyon kuralları:
- TC kimlik (11 hane) → [TC_REDACTED]
- Telefon numarası (TR) → [PHONE_REDACTED]
- Tam tarih (DD.MM.YYYY) → [DATE_REDACTED]
- chat_id (@lid veya @c.us) → SHA256 hash + pseudonym

Kullanım:
    python scripts/export_whatsapp_messages.py
    python scripts/export_whatsapp_messages.py --limit-chats 10  # test
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
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

WAHA_URL = os.getenv("WAHA_URL", "http://localhost:3000")
WAHA_KEY = os.getenv("WAHA_API_KEY", "")
WAHA_SESSION = os.getenv("WAHA_SESSION", "default")
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# PII patternleri
TC_REGEX = re.compile(r"\b[1-9]\d{10}\b")
PHONE_REGEX = re.compile(
    r"(?:\+?9?0?\s*)?5\d{2}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}"
)
DATE_REGEX = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2}\b")


def chat_hash(chat_id: str) -> str:
    """chat_id'nin SHA256 hash'i (deterministik pseudonymization)."""
    return hashlib.sha256(chat_id.encode()).hexdigest()[:12]


def redact_pii(text: str) -> str:
    """Mesaj metnindeki PII'yi maskele."""
    if not text:
        return ""
    text = TC_REGEX.sub("[TC_REDACTED]", text)
    text = PHONE_REGEX.sub("[PHONE_REDACTED]", text)
    text = DATE_REGEX.sub("[DATE_REDACTED]", text)
    return text


def get_chats(limit: int = 500) -> list[dict]:
    """Tüm chat'leri listele."""
    chats = []
    offset = 0
    page_size = min(500, limit)
    while len(chats) < limit:
        r = requests.get(
            f"{WAHA_URL}/api/{WAHA_SESSION}/chats",
            headers={"X-Api-Key": WAHA_KEY},
            params={"limit": page_size, "offset": offset},
            timeout=60,
        )
        r.raise_for_status()
        page = r.json()
        if not page:
            break
        chats.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return chats[:limit]


def get_messages(chat_id: str, limit: int = 1000) -> list[dict]:
    """Bir chat'in tüm mesajlarını paginate ile çek."""
    msgs = []
    page_size = 100
    download_media = "false"  # KVKK: medya değil, sadece metin

    # WAHA chat messages endpoint farklı session formatları kullanabilir
    # /api/{session}/chats/{chatId}/messages
    safe_chat_id = chat_id.replace("/", "_")  # URL safe
    url = f"{WAHA_URL}/api/{WAHA_SESSION}/chats/{safe_chat_id}/messages"

    offset = 0
    while len(msgs) < limit:
        try:
            r = requests.get(
                url,
                headers={"X-Api-Key": WAHA_KEY},
                params={
                    "limit": page_size,
                    "offset": offset,
                    "downloadMedia": download_media,
                },
                timeout=60,
            )
            if r.status_code != 200:
                break
            page = r.json()
            if not page:
                break
            msgs.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        except Exception:
            break

    return msgs[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit-chats", type=int, default=None,
        help="Test için ilk N chat (default: tümü)",
    )
    parser.add_argument(
        "--limit-msgs", type=int, default=1000,
        help="Her chat için max mesaj sayısı (default: 1000)",
    )
    args = parser.parse_args()

    if not WAHA_KEY:
        print(f"{RED}✗ WAHA_API_KEY yok{RESET}")
        return 1

    print(f"{YELLOW}WhatsApp Tüm Mesajları → JSONL Export (PII Redacted){RESET}")
    print(f"{DIM}WAHA: {WAHA_URL} | Session: {WAHA_SESSION}{RESET}")
    print()

    # Output dosyaları
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = REPORTS_DIR / f"whatsapp_messages_{stamp}.jsonl"
    summary_path = REPORTS_DIR / f"whatsapp_messages_{stamp}.summary.md"

    # 1. Chat listesi
    print(f"{DIM}1) Chat listesi çekiliyor...{RESET}")
    chats = get_chats(limit=args.limit_chats or 500)
    print(f"{GREEN}✓{RESET} {len(chats)} chat bulundu")
    print()

    # 2. Her chat için mesajlar
    print(f"{DIM}2) Her chat için mesajlar çekiliyor (PII redaction'lı)...{RESET}")

    total_msgs = 0
    total_doctor = 0
    total_patient = 0
    failed_chats = 0
    chat_summary = []

    with open(jsonl_path, "w", encoding="utf-8") as f:
        for i, chat in enumerate(chats, 1):
            # WAHA chat.id nested dict: {server, user, _serialized}
            id_raw = chat.get("id", {})
            if isinstance(id_raw, dict):
                chat_id = id_raw.get("_serialized") or id_raw.get("user", "")
            else:
                chat_id = str(id_raw)
            chat_name = chat.get("name") or chat_id
            ch_hash = chat_hash(chat_id)

            # Pseudonym: patient_registry'den isimle ara, yoksa hash'i kullan
            try:
                from patient_registry import get_default_registry
                from pii_crypto import short_pseudonym
                reg = get_default_registry()
                existing = reg.find_by_name(chat_name)
                if existing:
                    pseudonym = short_pseudonym(existing[0]["uuid"])
                else:
                    # Bilinmeyen kişi → hash bazlı placeholder
                    pseudonym = f"#chat-{ch_hash[:8]}"
            except Exception:
                pseudonym = f"#chat-{ch_hash[:8]}"

            try:
                msgs = get_messages(chat_id, limit=args.limit_msgs)
            except Exception as exc:
                failed_chats += 1
                msgs = []

            doctor_msg = patient_msg = 0
            for m in msgs:
                body = redact_pii(m.get("body") or "")
                from_me = bool(m.get("fromMe"))
                if from_me:
                    doctor_msg += 1
                else:
                    patient_msg += 1

                record = {
                    "chat_pseudonym": pseudonym,
                    "chat_id_hash": ch_hash,
                    "msg_id": m.get("id", ""),
                    "fromMe": from_me,
                    "timestamp": m.get("timestamp", 0),
                    "body": body,
                    "type": m.get("type", "text"),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                total_msgs += 1

            total_doctor += doctor_msg
            total_patient += patient_msg
            chat_summary.append({
                "pseudonym": pseudonym,
                "doctor": doctor_msg,
                "patient": patient_msg,
                "total": doctor_msg + patient_msg,
            })

            if i % 25 == 0 or i == len(chats):
                print(
                    f"  {i}/{len(chats)} chat | "
                    f"{total_msgs} mesaj | doktor: {total_doctor} | hasta: {total_patient}"
                )

    print()
    print(f"{GREEN}✓ Export tamamlandı{RESET}")
    print(f"  Dosya: {jsonl_path.name}")
    print(f"  Boyut: {jsonl_path.stat().st_size // 1024} KB")
    print()
    print(f"{YELLOW}━━━ Özet ━━━{RESET}")
    print(f"  Toplam chat: {len(chats)}")
    print(f"  Toplam mesaj: {total_msgs}")
    print(f"  Doktor mesajları: {total_doctor}")
    print(f"  Hasta mesajları: {total_patient}")
    if failed_chats:
        print(f"  Başarısız chat: {failed_chats}")

    # Summary markdown
    chat_summary.sort(key=lambda x: -x["total"])
    md = [
        f"# WhatsApp Mesaj Export Özeti",
        f"_Üretildi: {datetime.now().strftime('%d.%m.%Y %H:%M')}_  ",
        f"_Toplam: {len(chats)} chat, {total_msgs} mesaj_  ",
        f"_Doktor: {total_doctor} | Hasta: {total_patient}_",
        "",
        "## En Aktif 25 Chat",
        "",
        "| Pseudonym | Doktor | Hasta | Toplam |",
        "|-----------|--------|-------|--------|",
    ]
    for c in chat_summary[:25]:
        md.append(f"| {c['pseudonym']} | {c['doctor']} | {c['patient']} | {c['total']} |")

    summary_path.write_text("\n".join(md), encoding="utf-8")
    print(f"  Özet rapor: {summary_path.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
