"""
Modül 4: Paraşüt API v4 ile Otonom e-SMM

Kurulum:
    pip install requests

Çevre değişkenleri:
    PARASUT_CLIENT_ID       - Paraşüt OAuth2 client_id
    PARASUT_CLIENT_SECRET   - Paraşüt OAuth2 client_secret
    PARASUT_USERNAME        - Paraşüt kullanıcı e-postası
    PARASUT_PASSWORD        - Paraşüt kullanıcı şifresi
    PARASUT_COMPANY_ID      - Paraşüt firma ID (URL'den alınır)
    PARASUT_SMM_CATEGORY_ID - SMM için kullanılacak kategori ID
    JOB_POLL_INTERVAL_SEC   - Asenkron iş durumu sorgulama aralığı (varsayılan: 8)
    JOB_POLL_MAX_RETRIES    - Maksimum sorgulama denemesi (varsayılan: 30)
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

# Modül 3'ten WhatsApp gönderim fonksiyonunu içe aktar
from module3_whatsapp_communicator import send_whatsapp_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("esmm_generator")

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

PARASUT_BASE_URL = "https://api.parasut.com/v4"
PARASUT_TOKEN_URL = "https://api.parasut.com/oauth/token"

PARASUT_CLIENT_ID = os.getenv("PARASUT_CLIENT_ID", "")
PARASUT_CLIENT_SECRET = os.getenv("PARASUT_CLIENT_SECRET", "")
PARASUT_USERNAME = os.getenv("PARASUT_USERNAME", "")
PARASUT_PASSWORD = os.getenv("PARASUT_PASSWORD", "")
PARASUT_COMPANY_ID = os.getenv("PARASUT_COMPANY_ID", "")
PARASUT_SMM_CATEGORY_ID = os.getenv("PARASUT_SMM_CATEGORY_ID", "")

JOB_POLL_INTERVAL_SEC = int(os.getenv("JOB_POLL_INTERVAL_SEC", "8"))
JOB_POLL_MAX_RETRIES = int(os.getenv("JOB_POLL_MAX_RETRIES", "30"))

# ---------------------------------------------------------------------------
# Veri Yapıları
# ---------------------------------------------------------------------------

@dataclass
class TokenBundle:
    access_token: str
    token_type: str
    expires_at: float  # Unix timestamp


@dataclass
class CollectionRecord:
    """Tahsilat kaydı — dışarıdan tetikleyici bu nesneyi sağlar."""
    patient_name: str
    guardian_phone: str
    tax_id: str           # VKN (10 hane) veya TCKN (11 hane)
    amount: float         # TL cinsinden tutar
    description: str      # SMM açıklaması, örn: "Psikiyatri Muayenesi"
    appointment_date: str # ISO format: "2026-04-01"
    contact_id: Optional[str] = None  # Paraşüt contact_id (varsa önceden biliniyorsa)


# ---------------------------------------------------------------------------
# 1. OAuth2 Token Yönetimi
# ---------------------------------------------------------------------------

_token_cache: Optional[TokenBundle] = None


def get_access_token() -> str:
    """
    Paraşüt API v4 OAuth2 Resource Owner Password akışıyla token alır.
    Token geçerliyse önbellekten döner, süresi dolduysa yeniler.
    """
    global _token_cache

    now = time.time()
    if _token_cache and _token_cache.expires_at > now + 60:
        return _token_cache.access_token

    payload = {
        "grant_type": "password",
        "client_id": PARASUT_CLIENT_ID,
        "client_secret": PARASUT_CLIENT_SECRET,
        "username": PARASUT_USERNAME,
        "password": PARASUT_PASSWORD,
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
    }

    resp = requests.post(PARASUT_TOKEN_URL, data=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    _token_cache = TokenBundle(
        access_token=data["access_token"],
        token_type=data["token_type"],
        expires_at=now + int(data.get("expires_in", 7200)),
    )
    logger.info("Paraşüt OAuth2 token alındı (expires_in=%ds)", data.get("expires_in", 7200))
    return _token_cache.access_token


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _api_url(path: str) -> str:
    return f"{PARASUT_BASE_URL}/{PARASUT_COMPANY_ID}{path}"


# ---------------------------------------------------------------------------
# 2. e-Fatura Gelen Kutusu Sorgusu (Mükellef Kontrolü)
# ---------------------------------------------------------------------------

def is_e_invoice_taxpayer(tax_id: str) -> bool:
    """
    Verilen VKN/TCKN'nin e-Fatura mükellefi olup olmadığını sorgular.
    Mükellefse True, değilse False döner (e-SMM kesilmeli).
    """
    resp = requests.get(
        _api_url("/e_invoice_inboxes"),
        headers=_headers(),
        params={"filter[vkn]": tax_id},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    inboxes = data.get("data", [])
    result = len(inboxes) > 0
    logger.info(
        "e-Fatura mükellef sorgusu | VKN/TCKN: %s | Mükellef: %s",
        tax_id[-4:].rjust(len(tax_id), "*"),  # Son 4 hane göster
        result,
    )
    return result


# ---------------------------------------------------------------------------
# 3. Paraşüt Contact (Kişi/Firma) Yönetimi
# ---------------------------------------------------------------------------

def find_or_create_contact(record: CollectionRecord) -> str:
    """
    Hasta/veli için Paraşüt'te contact arar; yoksa oluşturur.
    Döner: contact_id
    """
    if record.contact_id:
        return record.contact_id

    # VKN/TCKN'ye göre ara
    resp = requests.get(
        _api_url("/contacts"),
        headers=_headers(),
        params={"filter[tax_number]": record.tax_id},
        timeout=15,
    )
    resp.raise_for_status()
    existing = resp.json().get("data", [])
    if existing:
        contact_id = existing[0]["id"]
        logger.info("Mevcut contact bulundu: %s", contact_id)
        return contact_id

    # Yeni contact oluştur
    # TCKN 11 haneli → bireysel (individual), VKN 10 haneli → kurumsal
    contact_type = "Person" if len(record.tax_id) == 11 else "Company"
    payload = {
        "data": {
            "type": "contacts",
            "attributes": {
                "name": record.patient_name,
                "contact_type": contact_type,
                "tax_number": record.tax_id,
                "account_type": "customer",
            },
        }
    }
    resp = requests.post(
        _api_url("/contacts"),
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    contact_id = resp.json()["data"]["id"]
    logger.info("Yeni contact oluşturuldu: %s (%s)", record.patient_name, contact_id)
    return contact_id


# ---------------------------------------------------------------------------
# 4. e-SMM Oluşturma (Asenkron POST)
# ---------------------------------------------------------------------------

def create_esmm(record: CollectionRecord, contact_id: str) -> str:
    """
    Paraşüt API v4 üzerinden e-SMM (Serbest Meslek Makbuzu) oluşturma isteği gönderir.
    İşlem asenkrondir; döner: trackable_job_id
    """
    issue_date = record.appointment_date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    payload = {
        "data": {
            "type": "sales_invoices",
            "attributes": {
                "item_type": "invoice",           # e-SMM türü
                "description": record.description,
                "issue_date": issue_date,
                "due_date": issue_date,
                "invoice_series": "SMM",
                "invoice_id": 0,                  # Paraşüt otomatik atar
                "currency": "TRL",
                "exchange_rate": 1,
                "withholding_rate": 0,
                "vat_withholding_rate": 0,
                "invoice_discount_type": "percentage",
                "invoice_discount": 0,
                "billing_address": "",
                "billing_phone": "",
                "billing_fax": "",
                "tax_office": "",
                "tax_number": record.tax_id,
                "country": "Turkey",
                "is_abroad": False,
                "e_invoice": False,    # e-SMM → False (e-Fatura değil)
                "e_archive": False,
                "e_smm": True,
            },
            "relationships": {
                "contact": {
                    "data": {"type": "contacts", "id": contact_id}
                },
                "details": {
                    "data": [
                        {
                            "type": "sales_invoice_details",
                            "attributes": {
                                "quantity": 1,
                                "unit_price": record.amount,
                                "vat_rate": 0,       # Psikiyatri muayenesi KDV'den muaf
                                "discount_type": "percentage",
                                "discount_value": 0,
                                "description": record.description,
                            },
                            "relationships": {
                                "category": {
                                    "data": {
                                        "type": "item_categories",
                                        "id": PARASUT_SMM_CATEGORY_ID,
                                    }
                                }
                            } if PARASUT_SMM_CATEGORY_ID else {},
                        }
                    ]
                },
            },
        }
    }

    resp = requests.post(
        _api_url("/sales_invoices"),
        headers=_headers(),
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    response_data = resp.json()

    # Asenkron işlem başlatma
    job_resp = requests.post(
        _api_url(f"/sales_invoices/{response_data['data']['id']}/issue_smm"),
        headers=_headers(),
        json={},
        timeout=15,
    )
    job_resp.raise_for_status()
    job_id = job_resp.json()["data"]["id"]
    logger.info("e-SMM oluşturma başlatıldı | invoice_id: %s | job_id: %s",
                response_data["data"]["id"], job_id)
    return job_id


# ---------------------------------------------------------------------------
# 5. Asenkron İş Durumu Takibi
# ---------------------------------------------------------------------------

def poll_job_until_done(job_id: str) -> dict:
    """
    Paraşüt trackable job'ını 'done' statüsüne geçene kadar periyodik sorgular.
    Başarıda iş sonuç verisini, başarısızlıkta exception fırlatır.
    """
    url = _api_url(f"/trackable_jobs/{job_id}")

    for attempt in range(1, JOB_POLL_MAX_RETRIES + 1):
        resp = requests.get(url, headers=_headers(), timeout=15)
        resp.raise_for_status()
        job_data = resp.json().get("data", {})
        status = job_data.get("attributes", {}).get("status", "")
        progress = job_data.get("attributes", {}).get("progress", 0)

        logger.info(
            "Job durumu [%s] | durum: %s | ilerleme: %s%% | deneme: %d/%d",
            job_id, status, progress, attempt, JOB_POLL_MAX_RETRIES,
        )

        if status == "done":
            return job_data
        if status in ("failed", "error"):
            errors = job_data.get("attributes", {}).get("errors", "bilinmiyor")
            raise RuntimeError(f"e-SMM oluşturma başarısız | job_id={job_id} | hatalar: {errors}")

        time.sleep(JOB_POLL_INTERVAL_SEC)

    raise TimeoutError(
        f"e-SMM job {JOB_POLL_MAX_RETRIES} denemede tamamlanamadı | job_id={job_id}"
    )


# ---------------------------------------------------------------------------
# 6. PDF Linki Çekme
# ---------------------------------------------------------------------------

def fetch_pdf_url(invoice_id: str) -> str:
    """
    Tamamlanan e-SMM'nin imzalı PDF indirme linkini çeker.
    Döner: PDF URL
    """
    resp = requests.get(
        _api_url(f"/sales_invoices/{invoice_id}"),
        headers=_headers(),
        params={"include": "active_e_document"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    # Önce included içindeki e_document'tan PDF linkini al
    for included in data.get("included", []):
        if included.get("type") == "e_smms":
            pdf_url = (
                included.get("attributes", {}).get("pdf_url")
                or included.get("attributes", {}).get("public_url")
            )
            if pdf_url:
                logger.info("PDF linki alındı: %s", pdf_url[:60] + "...")
                return pdf_url

    raise ValueError(f"PDF linki bulunamadı | invoice_id={invoice_id}")


def get_invoice_id_from_job(job_data: dict) -> str:
    """Tamamlanan job verisinden fatura ID'sini çıkarır."""
    # Paraşüt API'si job sonucunda 'result_id' veya relationships içinde id döner
    attrs = job_data.get("attributes", {})
    result_id = attrs.get("result_id") or attrs.get("resourceId")
    if result_id:
        return str(result_id)
    # Fallback: relationships
    relationships = job_data.get("relationships", {})
    resource = relationships.get("resource", {}).get("data", {})
    return str(resource.get("id", ""))


# ---------------------------------------------------------------------------
# 7. WhatsApp ile PDF İletimi
# ---------------------------------------------------------------------------

def send_pdf_via_whatsapp(phone: str, patient_name: str, pdf_url: str) -> dict:
    """
    Evolution API üzerinden e-SMM PDF'ini WhatsApp mesajı olarak iletir.
    PDF URL doküman olarak gönderilir; ek metin açıklaması eklenir.
    """
    from module3_whatsapp_communicator import (
        EVOLUTION_INSTANCE_NAME,
        _evo_headers,
        _evo_post,
        _normalize_phone,
    )

    normalized = _normalize_phone(phone)
    caption = (
        f"Merhaba {patient_name} velisi,\n"
        f"Seans makbuzunuz hazırlanmıştır. "
        f"İyi günler dileriz."
    )

    # Evolution API mediaMessage endpoint'i ile PDF gönder
    payload = {
        "number": normalized,
        "mediatype": "document",
        "mimetype": "application/pdf",
        "caption": caption,
        "media": pdf_url,
        "fileName": f"e-SMM_{patient_name.replace(' ', '_')}.pdf",
    }

    result = _evo_post(
        f"/message/sendMedia/{EVOLUTION_INSTANCE_NAME}", payload
    )
    logger.info("e-SMM PDF WhatsApp ile iletildi → %s", normalized)
    return result


# ---------------------------------------------------------------------------
# 8. Ana Orkestratör
# ---------------------------------------------------------------------------

def process_collection(record: CollectionRecord) -> dict:
    """
    Tahsilat sonrası uçtan uca e-SMM akışını yürütür:
      1. Mükellef kontrolü
      2. Contact bul veya oluştur
      3. e-SMM oluştur (asenkron POST)
      4. Job tamamlanana kadar bekle
      5. PDF linkini çek
      6. WhatsApp ile ilet

    Döner: {'invoice_id', 'pdf_url', 'whatsapp_status'}
    """
    logger.info("Tahsilat işleniyor: %s | %.2f TL", record.patient_name, record.amount)

    # 1. Mükellef kontrolü
    is_taxpayer = is_e_invoice_taxpayer(record.tax_id)
    if is_taxpayer:
        # e-Fatura mükellefi → bu modül kapsamı dışı, loglayıp çık
        logger.warning(
            "%s e-Fatura mükellefi — e-SMM yerine e-Fatura kesilmeli.", record.patient_name
        )
        return {"status": "e_invoice_required", "patient": record.patient_name}

    # 2. Contact
    contact_id = find_or_create_contact(record)
    record.contact_id = contact_id

    # 3. e-SMM oluştur
    job_id = create_esmm(record, contact_id)

    # 4. Job takibi
    job_data = poll_job_until_done(job_id)
    invoice_id = get_invoice_id_from_job(job_data)
    logger.info("e-SMM tamamlandı | invoice_id: %s", invoice_id)

    # 5. PDF linki
    pdf_url = fetch_pdf_url(invoice_id)

    # 6. WhatsApp iletimi
    wa_result = send_pdf_via_whatsapp(
        phone=record.guardian_phone,
        patient_name=record.patient_name,
        pdf_url=pdf_url,
    )

    result = {
        "status": "done",
        "patient": record.patient_name,
        "invoice_id": invoice_id,
        "pdf_url": pdf_url,
        "whatsapp_message_id": wa_result.get("key", {}).get("id", ""),
    }
    logger.info("Süreç tamamlandı: %s", json.dumps(result, ensure_ascii=False))
    return result


async def process_collection_async(record: CollectionRecord) -> dict:
    """
    process_collection'ın asyncio uyumlu sarmalayıcısı.
    Blocking I/O çağrılarını thread pool'da çalıştırır.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, process_collection, record)


# ---------------------------------------------------------------------------
# 9. CLI Arayüzü
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="e-SMM Otomasyon Modülü")
    parser.add_argument("--patient-name", required=True)
    parser.add_argument("--phone", required=True, help="Veli WhatsApp numarası")
    parser.add_argument("--tax-id", required=True, help="VKN (10) veya TCKN (11)")
    parser.add_argument("--amount", required=True, type=float, help="Tahsilat tutarı (TL)")
    parser.add_argument("--description", default="Çocuk ve Ergen Psikiyatrisi Muayenesi")
    parser.add_argument(
        "--date",
        default=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
        help="Randevu tarihi (YYYY-MM-DD)",
    )
    args = parser.parse_args()

    record = CollectionRecord(
        patient_name=args.patient_name,
        guardian_phone=args.phone,
        tax_id=args.tax_id,
        amount=args.amount,
        description=args.description,
        appointment_date=args.date,
    )

    result = process_collection(record)
    print(json.dumps(result, ensure_ascii=False, indent=2))
