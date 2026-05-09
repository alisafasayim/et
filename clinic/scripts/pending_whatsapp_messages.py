#!/usr/bin/env python3
"""
WhatsApp cevaplanmamış mesajları listele.

WAHA'dan tüm chat'leri çeker, son mesajı `fromMe: false` olanları
cevaplanmamış olarak işaretler. Markdown raporu üretir, en eski
cevap bekleyen önce.

Kullanım:
    # Tüm cevaplanmamış mesajlar
    python scripts/pending_whatsapp_messages.py

    # Son N gün içindeki
    python scripts/pending_whatsapp_messages.py --days 7

    # Belirli süre öncesi cevaplanmamış (ör. 2+ saat — kritik öncelik)
    python scripts/pending_whatsapp_messages.py --hours-old 2

    # Cevaplanmış olanlarla birlikte (denetim için)
    python scripts/pending_whatsapp_messages.py --include-answered

KVKK güvencesi:
- Çıktı yerel disk'te (reports/) — Notion'a gitmez
- Mesaj içeriği + telefon numarası raporda görünür (doktor için kritik info)
- BitLocker'lı disk'te tutmak şart
"""

import argparse
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


def fetch_chats() -> list[dict]:
    """WAHA'dan tüm chat'leri çek (pagination ile)."""
    chats: list[dict] = []
    offset = 0
    page_size = 100

    while True:
        r = requests.get(
            f"{WAHA_URL}/api/{WAHA_SESSION}/chats/overview",
            headers={"X-Api-Key": WAHA_KEY},
            params={"limit": page_size, "offset": offset},
            timeout=30,
        )
        r.raise_for_status()
        page = r.json()
        if not page:
            break
        chats.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    return chats


def is_pending(chat: dict, now_ts_ms: int, days_filter: int | None,
               hours_old_filter: float | None) -> bool:
    """Chat cevaplanmamış mı + filtrelere uygun mu?"""
    last = chat.get("lastMessage")
    if not isinstance(last, dict):
        return False

    # Son mesaj bizden geldiyse → cevaplanmış
    if last.get("fromMe"):
        return False

    # Boş mesaj atla
    body = last.get("body") or ""
    if not body.strip():
        return False

    # Zaman filtresi
    ts_ms = last.get("timestamp", 0)
    # WAHA timestamp formatı saniye veya milisaniye olabilir
    if ts_ms < 10**12:  # saniye ise ms'ye çevir
        ts_ms *= 1000

    age_ms = now_ts_ms - ts_ms

    if days_filter is not None:
        max_age_ms = days_filter * 24 * 3600 * 1000
        if age_ms > max_age_ms:
            return False

    if hours_old_filter is not None:
        min_age_ms = hours_old_filter * 3600 * 1000
        if age_ms < min_age_ms:
            return False

    return True


def format_chat(chat: dict, idx: int) -> str:
    """Markdown satırı: tek bir cevaplanmamış chat için."""
    last = chat.get("lastMessage", {})
    chat_id = chat.get("id", "?")
    name = chat.get("name") or chat.get("pushName") or chat_id
    body = (last.get("body") or "").replace("\n", " ").strip()
    body_preview = body[:200] + ("..." if len(body) > 200 else "")
    ts_ms = last.get("timestamp", 0)
    if ts_ms < 10**12:
        ts_ms *= 1000

    # Yerel saat
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone(timedelta(hours=3)))
    when = dt.strftime("%d.%m.%Y %H:%M")
    age = datetime.now(tz=timezone(timedelta(hours=3))) - dt
    age_str = (
        f"{age.days}g{age.seconds // 3600}s" if age.days > 0
        else f"{age.seconds // 3600}s{(age.seconds % 3600) // 60}d"
    )

    # WhatsApp deep link (mobil/desktop'ta direkt açar)
    # @lid formatı için doğrudan link verilemez, c.us için verilir
    if chat_id.endswith("@c.us"):
        phone = chat_id.replace("@c.us", "")
        wa_link = f"https://wa.me/{phone}"
    else:
        wa_link = ""

    md = f"### {idx}. {name}\n"
    md += f"- **Numara/ID:** `{chat_id}`\n"
    md += f"- **Mesaj zamanı:** {when}  _(geçen süre: {age_str})_\n"
    if wa_link:
        md += f"- **Cevapla:** [{wa_link}]({wa_link})\n"
    md += f"- **Mesaj:**\n  > {body_preview}\n"
    return md


def main() -> int:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="Sadece son N gün içinde gelen mesajları göster",
    )
    parser.add_argument(
        "--hours-old", type=float, default=None,
        help="Sadece N saatten beri cevaplanmamış mesajları göster (kritik)",
    )
    parser.add_argument(
        "--include-answered", action="store_true",
        help="Cevaplanmış chat'leri de listele (denetim/inceleme için)",
    )
    args = parser.parse_args()

    if not WAHA_KEY:
        print(f"{RED}✗ WAHA_API_KEY ayarlanmamış{RESET}")
        return 1

    print(f"{YELLOW}WhatsApp Cevap Bekleyen Mesajlar{RESET}")
    print(f"{DIM}WAHA: {WAHA_URL} | Session: {WAHA_SESSION}{RESET}")
    print()

    print(f"{DIM}Chat'ler çekiliyor...{RESET}")
    try:
        chats = fetch_chats()
    except requests.HTTPError as exc:
        print(f"{RED}✗ WAHA hatası: {exc}{RESET}")
        if exc.response.status_code == 400 and "store" in exc.response.text.lower():
            print(f"{YELLOW}  NOWEB store kapalı. Önce şunu yapın:{RESET}")
            print(f"  PUT /api/sessions/default -d '{{\"config\":{{\"noweb\":{{\"store\":{{\"enabled\":true,\"fullSync\":true}}}}}}}}'")
        return 1

    print(f"{GREEN}✓{RESET} {len(chats)} chat bulundu")
    print()

    now_ts_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    pending = [
        c for c in chats
        if is_pending(c, now_ts_ms, args.days, args.hours_old)
    ]
    answered = [c for c in chats if not is_pending(c, now_ts_ms, args.days, args.hours_old)]

    print(f"{RED}● Cevap bekleyen: {len(pending)}{RESET}")
    if args.include_answered:
        print(f"{DIM}● Cevaplanmış / boş: {len(answered)}{RESET}")
    print()

    # Sırala: en eski (uzun süre cevap bekleyen) önce
    def sort_key(c):
        ts = c.get("lastMessage", {}).get("timestamp", 0)
        return ts if ts > 10**12 else ts * 1000
    pending.sort(key=sort_key)

    # Markdown çıktı
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"pending_whatsapp_{stamp}.md"

    md_lines = [
        f"# WhatsApp Cevap Bekleyen Mesajlar",
        f"",
        f"_Üretildi: {datetime.now().strftime('%d.%m.%Y %H:%M')}_  ",
        f"_Toplam chat: {len(chats)} | Cevap bekleyen: **{len(pending)}**_",
        f"",
    ]
    if args.days is not None:
        md_lines.append(f"_Filtre: son {args.days} gün_")
        md_lines.append("")
    if args.hours_old is not None:
        md_lines.append(f"_Filtre: {args.hours_old}+ saat cevap bekleyen_")
        md_lines.append("")

    md_lines.append("---")
    md_lines.append("")

    if not pending:
        md_lines.append("✅ **Cevap bekleyen mesaj yok!**")
    else:
        md_lines.append(f"## 🚨 Cevap Bekleyen ({len(pending)})")
        md_lines.append("")
        md_lines.append("_(En uzun süre cevap bekleyen önce sıralandı)_")
        md_lines.append("")
        for i, c in enumerate(pending, 1):
            md_lines.append(format_chat(c, i))

    if args.include_answered and answered:
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")
        md_lines.append(f"## ✓ Cevaplanmış / Boş ({len(answered)})")
        md_lines.append("")
        for i, c in enumerate(answered[:50], 1):
            md_lines.append(format_chat(c, i))
        if len(answered) > 50:
            md_lines.append(f"\n_(... +{len(answered) - 50} fazlası)_\n")

    out_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"{GREEN}✓{RESET} Rapor: {out_path}")
    print()

    # Konsol özeti — ilk 5 cevap bekleyen
    if pending:
        print(f"{YELLOW}━━━ İlk 5 Cevap Bekleyen ━━━{RESET}")
        for i, c in enumerate(pending[:5], 1):
            last = c.get("lastMessage", {})
            name = c.get("name") or c.get("id", "?")[:30]
            body = (last.get("body") or "")[:60].replace("\n", " ")
            ts_ms = last.get("timestamp", 0)
            if ts_ms < 10**12:
                ts_ms *= 1000
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone(timedelta(hours=3)))
            when = dt.strftime("%d.%m %H:%M")
            print(f"  {i}. [{when}] {name}")
            print(f"     {body}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
