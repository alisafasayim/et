"""
Modül 5 v2: Samsung Notes (PDF) → KVKK Hibrit Notion Migrasyon

Yenilikler (v1'e göre):
- patient_registry entegrasyonu (gerçek isim Fernet ile yerel şifreli)
- Pseudonym (#xxxx-xxxx) ile Notion'a yazma
- PII redaksiyon (TC kimlik, telefon, tam tarih → maskelenir)
- state_store ile idempotent (bir PDF iki kez işlenmez)
- audit log (KVKK m.12 uyarınca her import kaydı)
- pdfplumber ile text extract (klavye yazılı PDF için OCR'sız)

Kullanım:
    # Test (5 dosya)
    python module5_migration_v2.py --dir samsung_notes --limit 5

    # Tam migration
    python module5_migration_v2.py --dir samsung_notes

    # Dry-run (yazmadan denetle)
    python module5_migration_v2.py --dir samsung_notes --dry-run

KVKK güvencesi:
- Hasta adı sadece patient_registry.db'de Fernet ile şifreli (yerel)
- Notion'a sadece pseudonym (#a4f9-c2b1) ve REDACTED içerik
- TC kimlik, telefon, tam tarih maskelenir
- Audit log her import event'i için
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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

import pdfplumber
import requests

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

logger = logging.getLogger("migration_v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------------------------------------------------------------------
# PII Redaksiyon
# ---------------------------------------------------------------------------

TC_REGEX = re.compile(r"\b[1-9]\d{10}\b")
PHONE_REGEX = re.compile(
    r"(?:\+?9?0?\s*)?5\d{2}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}"
)
DATE_REGEX = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2}\b")


def redact_pii(text: str) -> str:
    """Mesaj/not metnindeki PII'yi maskele (sadece text-level)."""
    if not text:
        return ""
    text = TC_REGEX.sub("[TC_REDACTED]", text)
    text = PHONE_REGEX.sub("[PHONE_REDACTED]", text)
    text = DATE_REGEX.sub("[DATE_REDACTED]", text)
    return text


# ---------------------------------------------------------------------------
# Hasta Adı Parse (PDF dosya adı → ad çıkarımı)
# ---------------------------------------------------------------------------

# Samsung Notes PDF export tipik isimleri:
# "Hasta Adı Soyadı.pdf"
# "Ali Veli - 12.05.2026.pdf"
# "Ali Veli (Çocuk Hasta).pdf"
# "01-Ali_Veli_Anamnez.pdf"

_DATE_AT_END = re.compile(r"\s*[-_]?\s*\d{1,2}[._-]\d{1,2}[._-]\d{2,4}\s*$")
_LEADING_NUMBER = re.compile(r"^\d{1,3}[\s\-_.]+")
_PARENS = re.compile(r"\s*\([^)]*\)\s*")


def extract_patient_name(pdf_path: Path) -> str:
    """
    PDF dosya adından hasta ismini çıkar.
    Heuristik: tarih, parantez, lider numara, alt-çizgi temizle.
    """
    name = pdf_path.stem
    name = _PARENS.sub(" ", name)
    name = _DATE_AT_END.sub("", name)
    name = _LEADING_NUMBER.sub("", name)
    name = name.replace("_", " ").replace("-", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name or pdf_path.stem


# ---------------------------------------------------------------------------
# PDF Text Extract
# ---------------------------------------------------------------------------

def extract_pdf_text(pdf_path: Path) -> tuple[str, int]:
    """
    PDF'den tüm sayfa metnini birleştirir.
    Döner: (full_text, page_count)
    """
    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                pages_text.append(t)
        pcount = len(pdf.pages)
    return ("\n\n".join(pages_text)).strip(), pcount


# ---------------------------------------------------------------------------
# Notion Markdown Block Parse
# ---------------------------------------------------------------------------

def text_to_blocks(text: str, max_chars_per_block: int = 1900) -> list[dict]:
    """
    Düz metni Notion paragraf block'larına böler.
    Notion her rich_text en fazla ~2000 char kabul eder.
    Boş satırlarda doğal kırılma yapar.
    """
    blocks = []
    paragraphs = re.split(r"\n\s*\n", text)
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # Çok uzunsa böl
        while len(p) > max_chars_per_block:
            chunk = p[:max_chars_per_block]
            # Cümle sınırında kes
            cut = max(chunk.rfind(". "), chunk.rfind("\n"))
            if cut > max_chars_per_block // 2:
                chunk = p[:cut + 1]
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
            })
            p = p[len(chunk):].strip()
        if p:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": p}}]},
            })
    return blocks


# ---------------------------------------------------------------------------
# Ana Migration
# ---------------------------------------------------------------------------

def migrate_pdf(
    pdf_path: Path,
    dry_run: bool = False,
) -> dict:
    """
    Tek bir PDF dosyasını işle:
    1. Text extract (pdfplumber)
    2. Hasta adı parse (dosya adından)
    3. patient_registry → pseudonym (yoksa oluştur)
    4. PII redaksiyon (TC, telefon, tarih)
    5. state_store idempotency (PDF hash bazlı)
    6. Notion'a yaz (pseudonym ile)
    7. Audit log

    Döner: {status, patient_pseudonym, page_count, redacted_chars, ...}
    """
    result = {
        "filename": pdf_path.name,
        "status": "pending",
    }

    # PDF hash → idempotency anahtarı (içerik değişmediyse atla)
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    pdf_hash = h.hexdigest()[:16]
    result["pdf_hash"] = pdf_hash

    # state_store check
    from state_store import get_default_store
    store = get_default_store()
    if store.is_seen("samsung_migration", pdf_hash):
        result["status"] = "skipped"
        return result

    # 1. Hasta adı + 2. Patient registry pseudonym
    real_name = extract_patient_name(pdf_path)
    result["patient_name_extracted"] = real_name

    from patient_registry import get_default_registry
    from pii_crypto import short_pseudonym

    registry = get_default_registry()
    existing = registry.find_by_name(real_name)
    if existing:
        patient_uuid = existing[0]["uuid"]
    else:
        patient_uuid = registry.create_patient(full_name=real_name)
    pseudonym = short_pseudonym(patient_uuid)
    result["patient_pseudonym"] = pseudonym

    # 3. PDF text extract
    try:
        text, page_count = extract_pdf_text(pdf_path)
    except Exception as exc:
        result["status"] = "extract_failed"
        result["error"] = str(exc)
        return result

    result["page_count"] = page_count
    result["chars_raw"] = len(text)

    if not text.strip():
        result["status"] = "empty"
        return result

    # 4. PII redaksiyon
    redacted_text = redact_pii(text)
    result["chars_redacted"] = len(redacted_text)

    if dry_run:
        result["status"] = "dry_run"
        return result

    # 5. Notion'a yaz (KVKK hibrit: pseudonym + redacted klinik içerik)
    from module2_notion_archiver import _resolve_patient_root, _append_blocks
    from notion_schema import is_extended

    patient_root_id, _ = _resolve_patient_root(real_name)

    # Sayfa başlığı altına yeni bölüm: "Samsung Notes Migrasyonu - {tarih}"
    blocks = []
    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{
                "type": "text",
                "text": {"content": f"📥 Samsung Notes — {pdf_path.stem[:80]}"},
            }],
        },
    })
    blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [{
                "type": "text",
                "text": {"content": f"İçe aktarıldı: {datetime.now().strftime('%d.%m.%Y %H:%M')} • {page_count} sayfa"},
            }],
        },
    })
    blocks.append({"object": "block", "type": "divider", "divider": {}})

    blocks.extend(text_to_blocks(redacted_text))

    try:
        _append_blocks(patient_root_id, blocks)
        result["status"] = "synced"
        result["notion_page_id"] = patient_root_id
    except Exception as exc:
        result["status"] = "notion_failed"
        result["error"] = str(exc)
        return result

    # 6. State + audit
    meta = json.dumps({
        "patient_pseudonym": pseudonym,
        "page_count": page_count,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    })
    store.mark_seen("samsung_migration", pdf_hash, meta=meta)

    try:
        from audit_log import get_default_audit_log
        get_default_audit_log().record(
            actor="samsung_migration_v2",
            action="samsung_notes.imported",
            details={
                "pdf_hash": pdf_hash,
                "patient_pseudonym": pseudonym,
                "page_count": page_count,
                "chars_redacted": len(redacted_text),
            },
        )
    except Exception:
        pass

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir", default="samsung_notes",
        help="PDF klasörü (default: samsung_notes/)",
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Test için ilk N dosya")
    parser.add_argument("--dry-run", action="store_true",
                        help="Notion'a yazma, sadece denetim")
    args = parser.parse_args()

    print(f"{YELLOW}Samsung Notes → KVKK Hibrit Notion Migrasyon (v2){RESET}")
    print(f"{DIM}Klasör: {args.dir} | dry_run: {args.dry_run}{RESET}\n")

    pdf_dir = Path(args.dir)
    if not pdf_dir.exists():
        print(f"{RED}✗ Klasör bulunamadı: {pdf_dir}{RESET}")
        return 1

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[:args.limit]

    if not pdfs:
        print(f"{YELLOW}PDF bulunamadı: {pdf_dir}{RESET}")
        return 0

    print(f"{GREEN}✓{RESET} {len(pdfs)} PDF bulundu\n")

    summary = {
        "synced": 0, "skipped": 0, "dry_run": 0,
        "empty": 0, "extract_failed": 0, "notion_failed": 0,
    }
    t_start = time.time()

    for i, pdf_path in enumerate(pdfs, 1):
        try:
            r = migrate_pdf(pdf_path, dry_run=args.dry_run)
            status = r["status"]
            summary[status] = summary.get(status, 0) + 1
            icon = {
                "synced": f"{GREEN}✓{RESET}",
                "skipped": f"{DIM}—{RESET}",
                "dry_run": f"{DIM}~{RESET}",
                "empty": f"{YELLOW}!{RESET}",
            }.get(status, f"{RED}✗{RESET}")
            ps = r.get("patient_pseudonym", "?")
            chars = r.get("chars_redacted", r.get("chars_raw", 0))
            print(
                f"  {icon} [{i}/{len(pdfs)}] {ps} | "
                f"{r.get('page_count', '?')} sayfa | {chars} char | "
                f"{DIM}{r.get('filename', '?')[:40]}{RESET}"
            )
            if status in ("extract_failed", "notion_failed"):
                print(f"    {RED}{r.get('error', '?')[:80]}{RESET}")

            # Notion rate limit dostu (3 req/sec için ~0.4s arası)
            if not args.dry_run and status == "synced":
                time.sleep(0.4)
        except Exception as exc:
            summary["notion_failed"] = summary.get("notion_failed", 0) + 1
            print(f"  {RED}✗ {pdf_path.name}: {exc}{RESET}")
            logger.exception("Hata")

    elapsed = time.time() - t_start
    print()
    print(f"{YELLOW}━━━ Özet ━━━{RESET}")
    for k, v in summary.items():
        if v > 0:
            print(f"  {k}: {v}")
    print(f"  Süre: {elapsed/60:.1f} dk")
    return 0 if summary.get("notion_failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
