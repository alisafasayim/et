"""
İlaç takibi modülü.

SOAP'ın plan.medication alanı serbest metin → ayrı bir Notion DB'ye
yapısal kayıt. Her hastanın aktif ilaçları takip edilir; aynı seansta
tetikledikleri otomatik kayıt edilir.

Bu modül opt-in: NOTION_MEDICATIONS_DATABASE_ID set edildiğinde
çalışır; aksi halde no-op.

Notion DB schema beklentisi:
    - "İlaç" (title)              — ilaç adı (örn: "Risperdal 1 mg")
    - "Hasta" (rich_text)         — hasta adı (relation property
                                    eklenirse de kullanılabilir; KISS
                                    için rich_text)
    - "Doz" (rich_text)           — "5 mg, akşam" gibi serbest metin
    - "Başlangıç" (date)
    - "Bitiş" (date, opsiyonel)   — devam eden için boş
    - "Durum" (select)            — Aktif | Sonlandırıldı | Değiştirildi
    - "Notlar" (rich_text)
"""

import logging
import os
import re
from datetime import datetime
from typing import Optional

from http_retry import raise_for_retry, with_retry

logger = logging.getLogger("medications")

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_MEDICATIONS_DATABASE_ID = os.getenv("NOTION_MEDICATIONS_DATABASE_ID", "")


def _headers() -> dict:
    if not NOTION_TOKEN:
        raise EnvironmentError("NOTION_TOKEN ayarlanmamış.")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


@with_retry()
def _post(endpoint: str, payload: dict) -> dict:
    import requests
    resp = requests.post(
        f"{NOTION_BASE_URL}{endpoint}",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    raise_for_retry(resp)
    return resp.json()


@with_retry()
def _patch(endpoint: str, payload: dict) -> dict:
    import requests
    resp = requests.patch(
        f"{NOTION_BASE_URL}{endpoint}",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    raise_for_retry(resp)
    return resp.json()


# ---------------------------------------------------------------------------
# 1. SOAP plan.medication serbest metni parse
# ---------------------------------------------------------------------------

# Yaygın çocuk-ergen psikiyatri ilaçları (genişletilebilir).
# Doz formatları: "20 mg", "0.5 mg", "20mg" (kısa).
_DOSE_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?\s*mg\b", re.IGNORECASE)
_DRUG_TOKEN_PATTERN = re.compile(
    r"\b("
    r"risperdal|risperidon|"
    r"concerta|metilfenidat|ritalin|"
    r"strattera|atomoksetin|"
    r"prozac|fluoksetin|"
    r"lustral|sertralin|"
    r"depakin|valproat|"
    r"lamictal|lamotrijin|"
    r"abilify|aripiprazol|"
    r"zoloft|"
    r"cipram|sitalopram|"
    r"melatonin"
    r")",
    re.IGNORECASE,
)


def parse_medications_from_text(text: str) -> list[dict]:
    """
    Serbest metinden olası ilaç + doz bilgisini ayıklar.

    Heuristik: cümlelerin her biri için bilinen ilaç tokeni + opsiyonel
    doz arar. Bulamazsa boş liste döner. Kullanıcı manuel olarak DB'de
    düzenleyebilir.
    """
    if not text:
        return []

    found: list[dict] = []
    # Cümle bazında parça parça incele. Decimal noktayı (ör: "0.5 mg")
    # bozmamak için split deseni nokta+boşluk veya nokta+yeni satır.
    for sentence in re.split(r"[;,\n]|\.\s|\.$", text):
        s = sentence.strip()
        if not s:
            continue
        drug_match = _DRUG_TOKEN_PATTERN.search(s)
        if not drug_match:
            continue
        dose_match = _DOSE_PATTERN.search(s)
        found.append(
            {
                "drug_name": drug_match.group(1).capitalize(),
                "dose": dose_match.group(0).replace(" ", "") if dose_match else "",
                "raw_sentence": s,
            }
        )
    return found


# ---------------------------------------------------------------------------
# 2. Notion sorgu / yazım
# ---------------------------------------------------------------------------

def list_active_medications(patient_name: str) -> list[dict]:
    """
    Hasta için DB'de "Aktif" durumdaki ilaç kayıtlarını döner.
    """
    if not NOTION_MEDICATIONS_DATABASE_ID:
        return []

    payload = {
        "filter": {
            "and": [
                {"property": "Hasta", "rich_text": {"equals": patient_name}},
                {"property": "Durum", "select": {"equals": "Aktif"}},
            ]
        },
        "page_size": 100,
    }
    try:
        result = _post(
            f"/databases/{NOTION_MEDICATIONS_DATABASE_ID}/query", payload
        )
    except Exception as exc:
        logger.warning("Aktif ilaç sorgusu başarısız: %s", exc)
        return []

    meds = []
    for row in result.get("results", []):
        props = row.get("properties", {})
        meds.append(
            {
                "page_id": row["id"],
                "drug_name": _title(props.get("İlaç")),
                "dose": _rich(props.get("Doz")),
                "start": _date(props.get("Başlangıç")),
                "notes": _rich(props.get("Notlar")),
            }
        )
    return meds


def _title(prop: Optional[dict]) -> str:
    if not prop:
        return ""
    items = prop.get("title", [])
    return "".join(i.get("plain_text", "") for i in items)


def _rich(prop: Optional[dict]) -> str:
    if not prop:
        return ""
    items = prop.get("rich_text", [])
    return "".join(i.get("plain_text", "") for i in items)


def _date(prop: Optional[dict]) -> str:
    if not prop:
        return ""
    d = prop.get("date") or {}
    return d.get("start", "")


def add_medication(
    patient_name: str,
    drug_name: str,
    dose: str = "",
    notes: str = "",
    start_date: Optional[str] = None,
    status: str = "Aktif",
) -> Optional[str]:
    """
    Yeni ilaç kaydı oluşturur. NOTION_MEDICATIONS_DATABASE_ID yoksa no-op.
    Döner: page_id veya None.
    """
    if not NOTION_MEDICATIONS_DATABASE_ID:
        logger.debug("İlaç DB'si yapılandırılmamış; ekleme atlandı.")
        return None

    start_date = start_date or datetime.now().date().isoformat()
    properties = {
        "İlaç": {"title": [{"text": {"content": drug_name[:200]}}]},
        "Hasta": {"rich_text": [{"text": {"content": patient_name[:200]}}]},
        "Başlangıç": {"date": {"start": start_date}},
        "Durum": {"select": {"name": status}},
    }
    if dose:
        properties["Doz"] = {"rich_text": [{"text": {"content": dose}}]}
    if notes:
        properties["Notlar"] = {"rich_text": [{"text": {"content": notes[:1900]}}]}

    payload = {
        "parent": {"database_id": NOTION_MEDICATIONS_DATABASE_ID},
        "properties": properties,
    }
    try:
        result = _post("/pages", payload)
    except Exception as exc:
        logger.error("İlaç kaydı oluşturulamadı (%s, %s): %s", patient_name, drug_name, exc)
        return None
    page_id = result["id"]
    logger.info("İlaç kaydı eklendi: %s | %s %s", patient_name, drug_name, dose)
    return page_id


def mark_medication_status(page_id: str, status: str) -> None:
    """Mevcut ilaç kaydının durumunu günceller (Sonlandırıldı / Değiştirildi)."""
    payload = {
        "properties": {
            "Durum": {"select": {"name": status}},
        }
    }
    if status != "Aktif":
        payload["properties"]["Bitiş"] = {
            "date": {"start": datetime.now().date().isoformat()}
        }
    try:
        _patch(f"/pages/{page_id}", payload)
    except Exception as exc:
        logger.error("İlaç durumu güncellenemedi (%s): %s", page_id, exc)


# ---------------------------------------------------------------------------
# 3. SOAP entegrasyonu
# ---------------------------------------------------------------------------

def reconcile_medications_from_soap(soap_note: dict) -> dict:
    """
    SOAP'ın plan.medication alanından ilaçları çıkarır ve mevcut aktif
    ilaçlarla karşılaştırır:
      - Yeni eklenenler → DB'ye yazılır (Aktif)
      - Artık bahsi geçmeyenler → Eskiden Aktif'tiyse "Sonlandırıldı"
        olarak işaretlenir (defansif: doktor SOAP'ta yazmadıysa
        sonlandırma çıkarımı bilgi amaçlı; "Değiştirildi" değil)

    Döner: {"added": [...], "ended": [...]}
    """
    if not NOTION_MEDICATIONS_DATABASE_ID:
        return {"added": [], "ended": [], "skipped": "no_db"}

    patient_name = soap_note.get("patient_name", "")
    if not patient_name or patient_name.lower() == "unknown":
        return {"added": [], "ended": [], "skipped": "no_patient"}

    medication_text = soap_note.get("soap", {}).get("plan", {}).get("medication", "")
    detected = parse_medications_from_text(medication_text)
    detected_names = {d["drug_name"].lower() for d in detected}

    # Mevcut aktif kayıtlar
    existing = list_active_medications(patient_name)
    existing_by_name = {m["drug_name"].lower(): m for m in existing if m["drug_name"]}

    added: list[dict] = []
    ended: list[dict] = []

    # 1. Yeni eklenenler
    for med in detected:
        key = med["drug_name"].lower()
        if key in existing_by_name:
            continue  # Zaten kayıtlı
        page_id = add_medication(
            patient_name=patient_name,
            drug_name=med["drug_name"],
            dose=med["dose"],
            notes=med["raw_sentence"],
        )
        if page_id:
            added.append({**med, "page_id": page_id})

    # 2. SOAP'ta hiç bahsedilmeyen mevcut aktifler — sonlandırılma adayı
    for name_lower, med in existing_by_name.items():
        if name_lower not in detected_names:
            mark_medication_status(med["page_id"], "Sonlandırıldı")
            ended.append(med)

    if added or ended:
        logger.info(
            "İlaç reconcile | %s | eklendi=%d | sonlandı=%d",
            patient_name, len(added), len(ended),
        )
    return {"added": added, "ended": ended}
