#!/usr/bin/env python3
"""
Forms → Notion senkronizasyonu (anamnez akışı).

GOOGLE_FORM_IDS env'inde tanımlı her form için yanıtları çeker;
yanıt veren hasta'ya göre Notion Form Responses DB'sine satır
ekler ve Hastalar DB'sine relation kurar. Idempotent.

Hasta eşleştirme heuristik (sırayla denenir):
  1. Form yanıtında "Adınız" / "Hasta Adı" / "Çocuğun Adı" sorusu varsa
  2. respondent_email var ise patient_registry'de email araması
  3. Hiçbiri yoksa "Bilinmeyen" pseudonym ile ekle, manuel işaretle

KVKK güvencesi:
- Hasta adı pseudonym'e çevrilir (#a4f9-c2b1)
- Notion'a sadece pseudonym yazılır
- Stdout'ta sadece pseudonym + form ID

Idempotency: state_store 'form_sync' namespace + response_id

Kullanım:
    python scripts/sync_forms_to_notion.py
    python scripts/sync_forms_to_notion.py --form-id <ID>
    python scripts/sync_forms_to_notion.py --dry-run
"""

import argparse
import json
import logging
import os
import sys
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

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

logger = logging.getLogger("forms_sync")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# Hasta adı içerebilecek soru başlığı kalıpları (sırayla aranır,
# küçük harfe çevrilmiş halleriyle eşleşir)
NAME_QUESTION_PATTERNS = [
    # En spesifik (form yapısı: "Çocuğunuzun: / Adı Soyadı")
    "adı soyadı", "adi soyadi", "ad soyad",
    # Diğer yaygın varyasyonlar
    "adınız", "adiniz", "isminiz", "hasta adı", "hasta adi",
    "çocuğun adı", "cocugun adi", "çocuğunuzun adı", "cocugunuzun adi",
    "çocuğunuzun", "cocugunuzun",
    "ergenin adı", "ergenin adi",
    "danışanın adı", "danisanin adi",
]


def get_forms_service():
    """Forms token'ı ile Google Forms API servisini döner."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_file = os.getenv(
        "GOOGLE_FORMS_TOKEN_FILE",
        os.getenv("GOOGLE_TOKEN_FILE", "token.json"),
    )
    if not Path(token_file).exists():
        raise FileNotFoundError(
            f"Forms token bulunamadı: {token_file}. "
            "Önce: python scripts/google_auth.py --service forms"
        )
    creds = Credentials.from_authorized_user_file(
        token_file,
        [
            "https://www.googleapis.com/auth/forms.body.readonly",
            "https://www.googleapis.com/auth/forms.responses.readonly",
        ],
    )
    return build("forms", "v1", credentials=creds, cache_discovery=False)


def _resolve_form_ids(cli_id: str | None) -> list[str]:
    """CLI argümanı veya env'den form ID listesi döner."""
    if cli_id:
        return [cli_id]
    multi = os.getenv("GOOGLE_FORM_IDS", "")
    if multi:
        return [fid.strip() for fid in multi.split(",") if fid.strip()]
    single = os.getenv("GOOGLE_ANAMNESIS_FORM_ID", "")
    return [single] if single else []


def _extract_patient_name_from_answers(answers: dict[str, str]) -> str | None:
    """Form yanıtlarından hasta adı içerebilecek soruyu bulur."""
    for question, answer in answers.items():
        if not answer or not answer.strip():
            continue
        q_lower = question.lower()
        for pattern in NAME_QUESTION_PATTERNS:
            if pattern in q_lower:
                return answer.strip()
    return None


def sync_response_to_notion(
    response: dict,
    form_title: str,
    dry_run: bool = False,
) -> dict:
    """
    Tek form yanıtını Notion Form Responses DB'sine senkronize eder.

    KVKK güvencesi: Notion'a yazılan ad pseudonym; gerçek isim
    patient_registry.db'de Fernet ile şifreli kalır.
    """
    response_id = response.get("response_id", "")
    answers = response.get("answers", {})
    submitted_at = response.get("submitted_at", "")

    real_name = _extract_patient_name_from_answers(answers) or "Bilinmeyen"

    result = {
        "response_id": response_id,
        "form_title": form_title,
        "submitted_at": submitted_at,
        "status": "pending",
    }

    from state_store import get_default_store
    store = get_default_store()
    if store.is_seen("form_sync", response_id):
        result["status"] = "skipped"
        result["patient_name"] = "(daha önce işlendi)"
        return result

    # Pseudonym her zaman üretilir, dry-run'da bile gerçek isim ekrana çıkmaz
    from module2_notion_archiver import _resolve_patient_root
    patient_root_id, pseudonym_display = _resolve_patient_root(real_name)
    result["patient_name"] = pseudonym_display

    if dry_run:
        result["status"] = "dry_run"
        return result

    from module2_notion_archiver import create_form_response_page
    from notion_schema import get_database_ids, is_extended

    if not (is_extended() and get_database_ids().form_responses):
        # Form Responses DB yok — sadece patient root'a kaydet, atla
        result["status"] = "patient_only"
        result["form_response_page_id"] = patient_root_id
    else:
        # KVKK PII redaction — sadece klinik alanlar Notion'a gider.
        # PII alanları (TC, ad, telefon, anne-baba ad/meslek, okul, vs.)
        # patient_registry'ye Fernet ile şifreli yazılır (yerel-only).
        from pii_classification import redact_pii
        clinical_only = redact_pii(answers)

        page_id = create_form_response_page(
            patient_page_id=patient_root_id,
            patient_name=pseudonym_display,
            submitted_at=submitted_at,
            answers=clinical_only,
            include_clinical_blocks=True,  # PII filtreli klinik alanlar OK
        )
        result["status"] = "synced"
        result["form_response_page_id"] = page_id
        result["clinical_field_count"] = len(clinical_only)
        result["pii_field_count"] = len(answers) - len(clinical_only)

    meta_json = json.dumps({
        "form_response_page_id": result.get("form_response_page_id", ""),
        "form_title": form_title,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "patient_pseudonym": pseudonym_display,
    })
    store.mark_seen("form_sync", response_id, meta=meta_json)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument("--form-id", help="Sadece bu form ID'sini işle")
    parser.add_argument("--dry-run", action="store_true",
                        help="Notion'a yazma, sadece ne yapılacağını göster")
    args = parser.parse_args()

    print(f"{YELLOW}Forms → Notion Senkronizasyonu{RESET}")
    print(f"{DIM}dry_run={args.dry_run}{RESET}\n")

    form_ids = _resolve_form_ids(args.form_id)
    if not form_ids:
        print(f"{RED}✗ Form ID bulunamadı (GOOGLE_FORM_IDS veya GOOGLE_ANAMNESIS_FORM_ID){RESET}")
        return 1

    try:
        service = get_forms_service()
    except Exception as exc:
        print(f"{RED}✗ Forms bağlantısı kurulamadı: {exc}{RESET}")
        return 1

    from module2_notion_archiver import fetch_form_responses

    overall = {"synced": 0, "skipped": 0, "patient_only": 0, "dry_run": 0, "failed": 0}

    for form_id in form_ids:
        try:
            form_meta = service.forms().get(formId=form_id).execute()
            form_title = form_meta.get("info", {}).get("title", form_id[:8])
            responses = fetch_form_responses(service, form_id)
        except Exception as exc:
            print(f"{RED}✗ Form çekilemedi {form_id[:8]}: {exc}{RESET}")
            overall["failed"] += 1
            continue

        print(f"{GREEN}✓{RESET} '{form_title}' ({len(responses)} yanıt)")
        for resp in responses:
            try:
                r = sync_response_to_notion(resp, form_title, dry_run=args.dry_run)
                status = r["status"]
                overall[status] = overall.get(status, 0) + 1
                icon = {
                    "synced": f"{GREEN}✓{RESET}",
                    "skipped": f"{DIM}—{RESET}",
                    "patient_only": f"{YELLOW}!{RESET}",
                    "dry_run": f"{DIM}~{RESET}",
                }.get(status, f"{RED}✗{RESET}")
                print(
                    f"    {icon} {r['patient_name']:<30} "
                    f"{r.get('submitted_at', '')[:10]:<12} "
                    f"{DIM}({status}){RESET}"
                )
            except Exception as exc:
                overall["failed"] += 1
                print(f"    {RED}✗{RESET} Hata: {exc}")
                logger.exception("Sync error %s", resp.get("response_id"))

    print()
    print(f"{YELLOW}━━━ Özet ━━━{RESET}")
    for status, count in overall.items():
        if count > 0:
            print(f"  {status}: {count}")
    return 0 if overall["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
