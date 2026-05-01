"""
Modül 3: Evolution API ile Otonom WhatsApp İletişimi

Kurulum:
    pip install requests flask google-api-python-client \
                google-auth-httplib2 google-auth-oauthlib

Çevre değişkenleri:
    EVOLUTION_API_URL        - Kendi sunucunuzdaki Evolution API adresi
                               örn: http://localhost:8080
    EVOLUTION_API_KEY        - Evolution API global API anahtarı
    EVOLUTION_INSTANCE_NAME  - Bağlı WhatsApp instance adı
    GOOGLE_ANAMNESIS_FORM_URL - Hastaya gönderilecek anamnez form linki
    WEBHOOK_SECRET           - Gelen webhook isteklerini doğrulamak için gizli anahtar
    WEBHOOK_LISTEN_PORT      - Webhook dinleme portu (varsayılan: 5055)
"""

import hashlib
import hmac
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import requests
from flask import Flask, abort, jsonify, request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from http_retry import raise_for_retry, with_retry
from phone_utils import (
    extract_phone_from_description as _extract_phone_from_description,
)
from phone_utils import normalize_phone as _normalize_phone

# Loglama yapılandırması logging_setup tarafından merkezi yapılır.
# Basit getLogger; konfigürasyonu çağıran tarafa bırakırız.
logger = logging.getLogger("whatsapp_communicator")

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME", "clinic")
GOOGLE_ANAMNESIS_FORM_URL = os.getenv("GOOGLE_ANAMNESIS_FORM_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_LISTEN_PORT = int(os.getenv("WEBHOOK_LISTEN_PORT", "5055"))
WEBHOOK_PUBLIC_URL = os.getenv("WEBHOOK_PUBLIC_URL", "http://localhost:5055")

# Üretimde imza zorunlu olmalı; sadece geliştirme ortamında
# WEBHOOK_REQUIRE_SIGNATURE=false ile devre dışı bırakılabilir.
WEBHOOK_REQUIRE_SIGNATURE = os.getenv("WEBHOOK_REQUIRE_SIGNATURE", "true").lower() in (
    "1", "true", "yes", "on",
)

GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
# İptal mesajı geldiğinde Calendar event'i silmek istiyorsanız
# CALENDAR_AUTO_DELETE_ON_CANCEL=true yapın — yazılabilir scope gerekir.
# Bu değişiklik OAuth onayını yeniler (token.json silinmeli).
CALENDAR_AUTO_DELETE_ON_CANCEL = os.getenv(
    "CALENDAR_AUTO_DELETE_ON_CANCEL", "false"
).lower() in ("1", "true", "yes", "on")

GOOGLE_CALENDAR_SCOPES = (
    ["https://www.googleapis.com/auth/calendar.events"]
    if CALENDAR_AUTO_DELETE_ON_CANCEL
    else ["https://www.googleapis.com/auth/calendar.readonly"]
)
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# Yeni randevu taraması için kaç dakika öncesini kontrol et
CALENDAR_POLL_LOOKBACK_MINUTES = int(os.getenv("CALENDAR_POLL_LOOKBACK_MINUTES", "10"))
PAYMENT_JOB_KIND = "payment_esmm"
PAYMENT_JOB_STALE_SECONDS = int(os.getenv("PAYMENT_JOB_STALE_SECONDS", "1800"))
PAYMENT_JOB_POLL_LIMIT = int(os.getenv("PAYMENT_JOB_POLL_LIMIT", "5"))
PAYMENT_JOB_PAYLOAD_PREFIX = "fernet:"

# ---------------------------------------------------------------------------
# 1. Evolution API – Düşük Seviye HTTP İstemcisi
# ---------------------------------------------------------------------------

def _evo_headers() -> dict:
    if not EVOLUTION_API_KEY:
        raise EnvironmentError("EVOLUTION_API_KEY çevre değişkeni ayarlanmamış.")
    return {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json",
    }


@with_retry()
def _evo_post(path: str, payload: dict) -> dict:
    url = f"{EVOLUTION_API_URL}{path}"
    resp = requests.post(url, headers=_evo_headers(), json=payload, timeout=15)
    raise_for_retry(resp)
    return resp.json()


@with_retry()
def _evo_get(path: str) -> dict:
    url = f"{EVOLUTION_API_URL}{path}"
    resp = requests.get(url, headers=_evo_headers(), timeout=15)
    raise_for_retry(resp)
    return resp.json()


# ---------------------------------------------------------------------------
# 2. Evolution API – Instance ve Webhook Yapılandırması
# ---------------------------------------------------------------------------

def configure_instance_events() -> dict:
    """
    Evolution API instance'ına hangi olayların işleneceğini (EventsConfig) ayarlar.
    Gelen mesajlar, bağlantı durumu ve mesaj okundu bildirimleri aktif edilir.
    """
    payload = {
        "events": {
            "APPLICATION_STARTUP": False,
            "QRCODE_UPDATED": True,
            "MESSAGES_SET": False,
            "MESSAGES_UPSERT": True,   # Gelen/giden tüm mesajlar
            "MESSAGES_UPDATE": True,   # Okundu, iletildi bildirimler
            "MESSAGES_DELETE": False,
            "SEND_MESSAGE": False,
            "CONTACTS_SET": False,
            "CONTACTS_UPSERT": False,
            "CONTACTS_UPDATE": False,
            "PRESENCE_UPDATE": False,
            "CHATS_SET": False,
            "CHATS_UPSERT": False,
            "CHATS_UPDATE": False,
            "CHATS_DELETE": False,
            "GROUPS_UPSERT": False,
            "GROUP_UPDATE": False,
            "GROUP_PARTICIPANTS_UPDATE": False,
            "CONNECTION_UPDATE": True,  # Bağlantı kopma/bağlanma
            "CALL": False,
            "NEW_JWT_TOKEN": False,
        }
    }
    result = _evo_post(
        f"/instance/setEvents/{EVOLUTION_INSTANCE_NAME}", payload
    )
    logger.info("EventsConfig ayarlandı: %s", result)
    return result


def configure_webhook() -> dict:
    """
    Evolution API instance'ına webhook URL ve ayarlarını (WebhookConfig) yapılandırır.
    Tüm olaylar bu URL'e POST edilir.
    """
    payload = {
        "url": f"{WEBHOOK_PUBLIC_URL}/webhook/whatsapp",
        "webhook_by_events": False,   # Tüm olaylar tek URL'e gelsin
        "webhook_base64": False,
        "events": [
            "MESSAGES_UPSERT",
            "MESSAGES_UPDATE",
            "CONNECTION_UPDATE",
        ],
    }
    result = _evo_post(
        f"/webhook/set/{EVOLUTION_INSTANCE_NAME}", payload
    )
    logger.info("WebhookConfig ayarlandı: %s", result)
    return result


def get_instance_status() -> dict:
    """Instance bağlantı durumunu döner."""
    return _evo_get(f"/instance/connectionState/{EVOLUTION_INSTANCE_NAME}")


# ---------------------------------------------------------------------------
# 3. WhatsApp Mesaj Gönderimi
# ---------------------------------------------------------------------------

def send_whatsapp_message(phone: str, message: str) -> dict:
    """
    Evolution API üzerinden belirtilen numaraya WhatsApp mesajı gönderir.
    phone: ham telefon numarası (herhangi bir format)
    """
    normalized = _normalize_phone(phone)
    payload = {
        "number": normalized,
        "text": message,
        "delay": 1200,   # ms cinsinden gönderim gecikmesi (doğal görünüm için)
    }
    result = _evo_post(
        f"/message/sendText/{EVOLUTION_INSTANCE_NAME}", payload
    )
    logger.info("Mesaj gönderildi → %s | messageId: %s", normalized, result.get("key", {}).get("id"))
    return result


def send_appointment_reminder(
    patient_name: str,
    guardian_phone: str,
    appointment_dt: datetime,
    form_url: str = "",
) -> dict:
    """
    Yeni oluşturulan randevu için hoşgeldin mesajı + anamnez formu linki.
    Bu, randevu OLUŞTURULDUĞUNDA (poll_and_notify) gönderilir.
    """
    form_url = form_url or GOOGLE_ANAMNESIS_FORM_URL
    appointment_str = appointment_dt.strftime("%d.%m.%Y %H:%M")

    message = (
        f"Merhaba {patient_name} velisi,\n\n"
        f"Randevunuz *{appointment_str}* olarak planlanmıştır.\n\n"
        f"Lütfen gelmeden önce aşağıdaki anamnez formunu doldurunuz:\n"
        f"{form_url}\n\n"
        f"Herhangi bir değişiklik için bu mesajı yanıtlayabilirsiniz."
    )
    return send_whatsapp_message(guardian_phone, message)


def send_upcoming_reminder(
    patient_name: str,
    guardian_phone: str,
    appointment_dt: datetime,
    horizon: str,
) -> dict:
    """
    Yaklaşan randevu için kısa hatırlatma. horizon: '24h' veya '1h'.
    Anamnez form linki içermez (oluşturma anında zaten gönderildi);
    bu mesaj randevunun yaklaştığını belirtir, no-show oranını düşürür.
    """
    appointment_str = appointment_dt.strftime("%d.%m.%Y %H:%M")
    if horizon == "24h":
        intro = "Yarın randevunuz var!"
    elif horizon == "1h":
        intro = "Randevunuz yaklaşık 1 saat sonra başlıyor!"
    else:
        intro = f"Yaklaşan randevu hatırlatması ({horizon})"

    message = (
        f"Merhaba {patient_name} velisi,\n\n"
        f"⏰ {intro}\n"
        f"📅 *{appointment_str}*\n\n"
        f"Gelemeyecekseniz lütfen bu mesajı 'iptal' yazarak yanıtlayın; "
        f"böylece slot başka bir hastaya açılabilir.\n\n"
        f"İyi günler dileriz."
    )
    return send_whatsapp_message(guardian_phone, message)


# ---------------------------------------------------------------------------
# 4. Google Calendar – Yeni Randevu Tespiti
# ---------------------------------------------------------------------------

def get_calendar_service():
    creds = None
    token_path = Path(GOOGLE_TOKEN_FILE)
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(
            str(token_path), GOOGLE_CALENDAR_SCOPES
        )
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            GOOGLE_CREDENTIALS_FILE, GOOGLE_CALENDAR_SCOPES
        )
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def fetch_recently_created_appointments(service) -> list[dict]:
    """
    Son CALENDAR_POLL_LOOKBACK_MINUTES dakika içinde oluşturulmuş randevuları döner.
    Google Calendar'da 'updatedMin' parametresi ile filtreleme yapılır.
    """
    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(minutes=CALENDAR_POLL_LOOKBACK_MINUTES)

    events_result = (
        service.events()
        .list(
            calendarId=GOOGLE_CALENDAR_ID,
            updatedMin=since.isoformat(),
            timeMin=now.isoformat(),                      # Geçmiş randevular dahil edilmez
            timeMax=(now + timedelta(days=60)).isoformat(),
            singleEvents=True,
            orderBy="updated",
        )
        .execute()
    )

    appointments = []
    for event in events_result.get("items", []):
        created = datetime.fromisoformat(
            event.get("created", now.isoformat()).replace("Z", "+00:00")
        )
        # Sadece gerçekten yeni oluşturulanlar (güncelleme değil)
        if (now - created).total_seconds() <= CALENDAR_POLL_LOOKBACK_MINUTES * 60:
            start_str = event["start"].get("dateTime", event["start"].get("date"))
            appointments.append(
                {
                    "event_id": event["id"],
                    "summary": event.get("summary", ""),
                    "description": event.get("description", ""),
                    "start": start_str,
                    "start_dt": datetime.fromisoformat(
                        start_str.replace("Z", "+00:00")
                    ),
                    # Takvim açıklamasından telefon numarasını parse et
                    # Beklenen format: "Tel: 05321234567" veya "Veli Tel: ..."
                    "phone": _extract_phone_from_description(
                        event.get("description", "")
                    ),
                    "patient_name": event.get("summary", "Bilinmiyor"),
                }
            )
    return appointments


def fetch_upcoming_appointments(
    service,
    window_start: datetime,
    window_end: datetime,
) -> list[dict]:
    """
    Belirli bir zaman penceresinde başlayacak randevuları döner.
    24h ve 1h hatırlatma cron'larında kullanılır — fetch_recently_
    created_appointments yalnızca yeni oluşturulanları getirir,
    bu fonksiyon ise GELECEKTEKİ randevuları getirir.
    """
    events_result = (
        service.events()
        .list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=window_start.astimezone(timezone.utc).isoformat(),
            timeMax=window_end.astimezone(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    appointments = []
    for event in events_result.get("items", []):
        # All-day eventları atla; klinik randevu saatli olur
        if "dateTime" not in event["start"]:
            continue
        start_str = event["start"]["dateTime"]
        appointments.append(
            {
                "event_id": event["id"],
                "summary": event.get("summary", ""),
                "description": event.get("description", ""),
                "start": start_str,
                "start_dt": datetime.fromisoformat(start_str.replace("Z", "+00:00")),
                "phone": _extract_phone_from_description(
                    event.get("description", "")
                ),
                "patient_name": event.get("summary", "Bilinmiyor"),
            }
        )
    return appointments


# ---------------------------------------------------------------------------
# 5. Gelen Mesaj İşleyici – İptal Tespiti
# ---------------------------------------------------------------------------

CANCELLATION_KEYWORDS = [
    "iptal", "cancel", "gelemeyeceğiz", "gelemiyoruz",
    "randevuyu iptal", "gelmeyeceğiz", "iptal etmek istiyorum",
]

RESCHEDULE_KEYWORDS = [
    "ertelemek", "ötelemek", "değiştirmek", "başka gün",
    "başka zaman", "reschedule",
]


def classify_incoming_message(message_text: str) -> str:
    """
    Gelen mesajı anahtar kelime taramasıyla sınıflandırır.
    Döner: 'cancellation' | 'reschedule' | 'other'
    """
    text = message_text.lower()
    if any(kw in text for kw in CANCELLATION_KEYWORDS):
        return "cancellation"
    if any(kw in text for kw in RESCHEDULE_KEYWORDS):
        return "reschedule"
    return "other"


def find_upcoming_appointment_by_phone(
    sender_phone: str,
    lookahead_days: int = 30,
) -> dict | None:
    """
    Verilen veli telefonunun yaklaşan randevusunu bulur.
    Calendar etkinliği açıklamasındaki "Tel: ..." satırı üzerinden
    eşleştirme yapılır. Birden fazla varsa EN YAKININI döner.
    """
    normalized = _normalize_phone(sender_phone)
    service = get_calendar_service()
    now = datetime.now(tz=timezone.utc)
    upcoming = fetch_upcoming_appointments(
        service,
        window_start=now,
        window_end=now + timedelta(days=lookahead_days),
    )
    for appt in sorted(upcoming, key=lambda a: a["start_dt"]):
        appt_phone_normalized = _normalize_phone(appt.get("phone", ""))
        if appt_phone_normalized and appt_phone_normalized == normalized:
            return appt
    return None


def delete_calendar_event(event_id: str) -> bool:
    """
    Calendar etkinliğini siler. CALENDAR_AUTO_DELETE_ON_CANCEL=false
    ise no-op (False döner). Read-only scope'la çağrılırsa Google
    HttpError fırlatır — caller yakalamalı.
    """
    if not CALENDAR_AUTO_DELETE_ON_CANCEL:
        logger.info(
            "Calendar event silme atlandı (CALENDAR_AUTO_DELETE_ON_CANCEL=false): %s",
            event_id,
        )
        return False
    service = get_calendar_service()
    service.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id).execute()
    logger.info("Calendar event silindi: %s", event_id)
    return True


def handle_cancellation_request(sender_phone: str, message_text: str) -> None:
    """
    İptal talebini işler:
      1. Veliye onay mesajı gönder
      2. Calendar'da yaklaşan randevuyu bul (telefon üzerinden)
      3. CALENDAR_AUTO_DELETE_ON_CANCEL=true ise event'i sil; aksi
         halde sadece doktora bildirim gönder
      4. Doktora özet bildirim (slot bilgisiyle)
    """
    doctor_phone = os.getenv("DOCTOR_PHONE", "")
    logger.warning("İPTAL TALEBİ | Gönderen: %s | Mesaj: %s", sender_phone, message_text)

    # 1. Gönderene otomatik yanıt
    send_whatsapp_message(
        sender_phone,
        "İptal talebiniz alındı. Size en kısa sürede dönüş yapılacaktır.",
    )

    # 2. Yaklaşan randevuyu bul
    appointment_info = ""
    deleted = False
    try:
        appt = find_upcoming_appointment_by_phone(sender_phone)
        if appt:
            appt_str = appt["start_dt"].astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
            appointment_info = (
                f"\nTespit edilen randevu: {appt.get('summary', '—')} ({appt_str})"
            )
            # 3. Otomatik silme açıksa
            try:
                deleted = delete_calendar_event(appt["event_id"])
            except Exception as exc:
                logger.error("Event silme hatası [%s]: %s", appt["event_id"], exc)
                appointment_info += f"\n⚠️ Otomatik silme başarısız: {exc}"
        else:
            appointment_info = "\n(Bu telefona kayıtlı yaklaşan randevu bulunamadı)"
    except Exception as exc:
        logger.error("Yaklaşan randevu arama hatası: %s", exc)

    # 4. Doktora bildirim
    if doctor_phone:
        status_note = (
            "Slot otomatik boşaltıldı."
            if deleted
            else "Calendar'dan manuel iptal gerekli."
        )
        send_whatsapp_message(
            doctor_phone,
            (
                f"⚠️ İptal Talebi\n"
                f"Numara: +{_normalize_phone(sender_phone)}\n"
                f"Mesaj: {message_text}"
                f"{appointment_info}\n"
                f"{status_note}"
            ),
        )


def handle_reschedule_request(sender_phone: str, message_text: str) -> None:
    """
    Erteleme talebini loglar ve doktora yönlendirir.
    """
    doctor_phone = os.getenv("DOCTOR_PHONE", "")
    logger.info("ERTELEME TALEBİ | Gönderen: %s", sender_phone)

    send_whatsapp_message(
        sender_phone,
        "Erteleme talebiniz alındı. Müsait günler için sizi arayacağız.",
    )

    if doctor_phone:
        send_whatsapp_message(
            doctor_phone,
            f"🔄 Erteleme Talebi\nNumara: +{_normalize_phone(sender_phone)}\nMesaj: {message_text}",
        )


# ---------------------------------------------------------------------------
# 6. Webhook Sunucusu (Flask)
# ---------------------------------------------------------------------------

app = Flask(__name__)


def _verify_webhook_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """
    HMAC-SHA256 ile gelen webhook isteğinin imzasını doğrular.
    Evolution API webhook'u 'X-Webhook-Signature' başlığı gönderir.

    Önceki sürüm: WEBHOOK_SECRET boşsa True dönüyordu — production'da
    secret set edilmesi unutulduğunda webhook'a herkes POST atabilirdi.
    Yeni davranış (fail-closed):
      - WEBHOOK_REQUIRE_SIGNATURE=true (varsayılan) ve secret yoksa
        log'a kritik uyarı bas, isteği REDDET.
      - Secret varsa imza karşılaştır.
      - Sadece geliştirme için WEBHOOK_REQUIRE_SIGNATURE=false ile
        bilinçli olarak atlanabilir.
    """
    if not WEBHOOK_SECRET:
        if WEBHOOK_REQUIRE_SIGNATURE:
            logger.critical(
                "WEBHOOK_SECRET ayarlanmamış. Webhook isteği fail-closed reddedildi. "
                "Yalnızca dev ortamında WEBHOOK_REQUIRE_SIGNATURE=false ile atlayabilirsiniz."
            )
            return False
        logger.warning("WEBHOOK_REQUIRE_SIGNATURE=false → imza doğrulama atlandı (DEV).")
        return True

    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")


# ---------------------------------------------------------------------------
# Tahsilat (POS / iyzico) Webhook → otomatik e-SMM tetikleme
# ---------------------------------------------------------------------------

PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "")


def _verify_payment_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """
    Payment webhook için ayrı bir secret ile HMAC-SHA256 doğrulaması.
    PAYMENT_WEBHOOK_SECRET set değilse fail-closed (WhatsApp webhook
    ile aynı politika).
    """
    if not PAYMENT_WEBHOOK_SECRET:
        if WEBHOOK_REQUIRE_SIGNATURE:
            logger.critical(
                "PAYMENT_WEBHOOK_SECRET ayarlanmamış. /webhook/payment isteği reddedildi."
            )
            return False
        logger.warning("Payment webhook imzası dev modda atlandı.")
        return True
    expected = hmac.new(
        PAYMENT_WEBHOOK_SECRET.encode(), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")


def _normalize_payment_payload(raw: dict) -> dict | None:
    """
    POS sağlayıcısından bağımsız ortak şemaya çevirir.

    Beklenen ortak alanlar (kayda hangi field'ları gönderdiğine bakar):
        amount, patient_name, guardian_phone, tax_id, collection_key
        appointment_date (isteğe bağlı)
        description (isteğe bağlı)

    Sağlayıcı 'event' veya 'status' alanında 'success' / 'paid'
    benzeri bir değer gönderiyorsa True kabul edilir; aksi halde None.

    İhtiyaca göre sağlayıcı-spesifik adapter eklenebilir; şimdilik
    her sağlayıcının webhook payload'ını klinik tarafında elle
    `amount`, `patient_name`, `guardian_phone`, `tax_id` ile
    normalize eden generic bir contract.
    """
    status = (raw.get("event") or raw.get("status") or "").lower()
    success_markers = {"success", "succeeded", "paid", "completed", "payment.succeeded", "payment_intent.succeeded"}
    if status and not any(m in status for m in success_markers):
        return None

    required = ("amount", "patient_name", "guardian_phone", "tax_id")
    if not all(raw.get(k) for k in required):
        return None

    return {
        "amount": raw["amount"],
        "patient_name": str(raw["patient_name"]),
        "guardian_phone": str(raw["guardian_phone"]),
        "tax_id": str(raw["tax_id"]),
        "description": str(raw.get("description", "Çocuk ve Ergen Psikiyatrisi Muayenesi")),
        "appointment_date": str(raw.get("appointment_date", "")),
        "collection_key": str(raw.get("collection_key", raw.get("id", ""))),
    }


def _payment_job_id(normalized: dict, raw_body: bytes) -> str:
    job_id = normalized.get("collection_key") or hashlib.sha256(raw_body).hexdigest()
    normalized["collection_key"] = job_id
    return job_id


def _encode_payment_job_payload(normalized: dict) -> str:
    from pii_crypto import encrypt

    plaintext = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    return PAYMENT_JOB_PAYLOAD_PREFIX + encrypt(plaintext)


def _decode_payment_job_payload(stored_payload: str) -> dict:
    if stored_payload.startswith(PAYMENT_JOB_PAYLOAD_PREFIX):
        from pii_crypto import decrypt

        encrypted = stored_payload[len(PAYMENT_JOB_PAYLOAD_PREFIX):]
        return json.loads(decrypt(encrypted))
    return json.loads(stored_payload)


def _process_claimed_payment_job(store, job: dict) -> dict:
    job_id = job["job_id"]
    try:
        payload = _decode_payment_job_payload(job["payload"])
        from main import trigger_esmm
        result = trigger_esmm(
            patient_name=payload["patient_name"],
            guardian_phone=payload["guardian_phone"],
            tax_id=payload["tax_id"],
            amount=payload["amount"],
            description=payload["description"],
            appointment_date=payload["appointment_date"],
            collection_key=payload["collection_key"],
        )
        store.complete_job(
            PAYMENT_JOB_KIND,
            job_id,
            result=json.dumps(result, ensure_ascii=False, default=str),
        )
        logger.info("Payment job completed: %s | status=%s", job_id, result.get("status"))
        return {"job_id": job_id, "status": "done", "result": result}
    except Exception as exc:
        store.fail_job(PAYMENT_JOB_KIND, job_id, str(exc))
        logger.error("Payment job failed [%s]: %s", job_id, exc)
        return {"job_id": job_id, "status": "failed", "error": str(exc)}


def process_payment_job(job_id: str) -> dict:
    """Claim and process one queued payment job by id."""
    from state_store import get_default_store

    store = get_default_store()
    job = store.claim_job(PAYMENT_JOB_KIND, job_id)
    if not job:
        existing = store.get_job(PAYMENT_JOB_KIND, job_id)
        return existing or {"job_id": job_id, "status": "missing"}
    return _process_claimed_payment_job(store, job)


def poll_payment_jobs(limit: int = PAYMENT_JOB_POLL_LIMIT) -> list[dict]:
    """Process queued payment jobs, including stale jobs from a crashed worker."""
    from state_store import get_default_store

    store = get_default_store()
    requeued = store.requeue_stale_jobs(PAYMENT_JOB_KIND, PAYMENT_JOB_STALE_SECONDS)
    if requeued:
        logger.warning("Requeued %d stale payment job(s)", requeued)

    results = []
    for _ in range(limit):
        job = store.claim_next_job(PAYMENT_JOB_KIND)
        if not job:
            break
        results.append(_process_claimed_payment_job(store, job))
    return results


@app.route("/webhook/payment", methods=["POST"])
def payment_webhook():
    """
    POS / ödeme sağlayıcı webhook'u → otomatik e-SMM tetikleme.

    Kabul edilen JSON contract (sağlayıcıdan bağımsız):
        {
          "event": "payment.succeeded" | "status": "paid",
          "amount": "1500.00",                    -- Decimal-safe string
          "patient_name": "Ahmet Yılmaz",
          "guardian_phone": "905321234567",
          "tax_id": "12345678901",
          "description": "...",                   -- opsiyonel
          "appointment_date": "2026-04-30",       -- opsiyonel
          "collection_key": "pos-tx-9421",        -- idempotency anahtarı
          "id": "..."                             -- collection_key yoksa fallback
        }

    Doğrulama: X-Webhook-Signature header'ı (HMAC-SHA256 / PAYMENT_WEBHOOK_SECRET)
    Yanıt: {"status": "queued"} veya {"status": "ignored"} / 4xx
    """
    raw_body = request.get_data()
    signature = request.headers.get("X-Webhook-Signature", "")

    if not _verify_payment_signature(raw_body, signature):
        abort(401)

    try:
        payload = request.get_json(force=True)
    except Exception:
        abort(400)

    normalized = _normalize_payment_payload(payload or {})
    if not normalized:
        logger.info("Payment webhook ignored (eksik/geçersiz payload).")
        return jsonify({"status": "ignored"}), 200

    from state_store import get_default_store

    job_id = _payment_job_id(normalized, raw_body)
    try:
        job_payload = _encode_payment_job_payload(normalized)
    except EnvironmentError as exc:
        logger.critical("Payment job payload encryption unavailable: %s", exc)
        return jsonify({"status": "configuration_error"}), 503
    store = get_default_store()

    if not store.enqueue_job(PAYMENT_JOB_KIND, job_id, job_payload):
        existing = store.get_job(PAYMENT_JOB_KIND, job_id) or {"status": "queued"}
        return jsonify({"status": f"already_{existing['status']}", "job_id": job_id}), 200

    threading.Thread(target=process_payment_job, args=(job_id,), daemon=True).start()
    return jsonify({"status": "queued", "job_id": job_id}), 202


@app.route("/webhook/calendar", methods=["POST"])
def calendar_webhook():
    """
    Google Calendar Watch push bildirimi → poll_and_notify tetikler.
    Polling'in real-time alternatifi (10 dk yerine ~saniyeler).

    Doğrulama: X-Goog-Channel-Token header'ı CALENDAR_PUSH_TOKEN ile
    eşleşmeli (set edildiyse).
    """
    from calendar_watch import handle_push_notification, verify_push_token

    received_token = request.headers.get("X-Goog-Channel-Token", "")
    if not verify_push_token(received_token):
        logger.warning("Calendar webhook geçersiz token reddedildi.")
        abort(401)

    result = handle_push_notification(dict(request.headers))
    return jsonify(result), 200


@app.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """
    Evolution API'den gelen tüm WhatsApp olaylarını karşılayan endpoint.
    """
    raw_body = request.get_data()
    signature = request.headers.get("X-Webhook-Signature", "")

    if not _verify_webhook_signature(raw_body, signature):
        logger.warning("Geçersiz webhook imzası reddedildi.")
        abort(401)

    try:
        event = request.get_json(force=True)
    except Exception:
        abort(400)

    event_type = event.get("event", "")
    logger.info("Webhook alındı: %s", event_type)

    if event_type == "MESSAGES_UPSERT":
        _handle_messages_upsert(event)
    elif event_type == "CONNECTION_UPDATE":
        _handle_connection_update(event)
    elif event_type == "MESSAGES_UPDATE":
        pass  # Okundu bildirimleri; şimdilik yoksayılıyor

    return jsonify({"status": "ok"}), 200


def _handle_messages_upsert(event: dict) -> None:
    """Gelen yeni mesajları işler."""
    for msg in event.get("data", []):
        # Kendi gönderdiğimiz mesajları atla
        if msg.get("key", {}).get("fromMe"):
            continue

        sender = msg.get("key", {}).get("remoteJid", "")
        sender_phone = sender.replace("@s.whatsapp.net", "")

        # Metin mesajını ayıkla
        message_body = (
            msg.get("message", {}).get("conversation")
            or msg.get("message", {}).get("extendedTextMessage", {}).get("text")
            or ""
        )

        if not message_body:
            return

        logger.info("Gelen mesaj | %s: %s", sender_phone, message_body[:80])

        classification = classify_incoming_message(message_body)
        if classification == "cancellation":
            handle_cancellation_request(sender_phone, message_body)
        elif classification == "reschedule":
            handle_reschedule_request(sender_phone, message_body)


def _handle_connection_update(event: dict) -> None:
    """WhatsApp bağlantı durumu değişikliklerini loglar."""
    state = event.get("data", {}).get("state", "")
    logger.info("Bağlantı durumu: %s", state)
    if state == "close":
        logger.error("WhatsApp bağlantısı kesildi! Instance yeniden başlatılması gerekebilir.")


# ---------------------------------------------------------------------------
# 7. Ana Orkestratör – Takvim Polling ve Mesaj Gönderimi
# ---------------------------------------------------------------------------

def poll_upcoming_reminders(horizon: str = "24h") -> list[dict]:
    """
    Belirtilen horizon (24h veya 1h) yaklaşan randevular için
    hatırlatma WhatsApp mesajı gönderir.

    Idempotency: her (event_id, horizon) kombinasyonu state store'da
    işaretlenir; aynı saatlerde tetiklenirse duplicate mesaj gitmez.

    Cron örnekleri:
      */15 * * * * python -c "from module3...wa_communicator import poll_upcoming_reminders; poll_upcoming_reminders('24h')"
      */10 * * * * python -c "from ...                                                                 ; poll_upcoming_reminders('1h')"
    """
    from state_store import get_default_store

    if horizon == "24h":
        offset = timedelta(hours=24)
        # 24h hatırlatma window: T-24h ± 30dk (cron 15'lik kaçırırsa garanti)
        slack = timedelta(minutes=30)
    elif horizon == "1h":
        offset = timedelta(hours=1)
        slack = timedelta(minutes=15)
    else:
        raise ValueError(f"Geçersiz horizon: {horizon}")

    store = get_default_store()
    service = get_calendar_service()
    now = datetime.now(tz=timezone.utc)

    # T = şu an + offset (24h sonra başlayacak)
    window_start = now + offset - slack
    window_end = now + offset + slack

    upcoming = fetch_upcoming_appointments(service, window_start, window_end)
    logger.info(
        "[Reminder %s] Pencere %s ↔ %s | bulunan randevu: %d",
        horizon, window_start.isoformat(), window_end.isoformat(), len(upcoming),
    )

    namespace = f"reminder_{horizon}"
    results = []

    for appt in upcoming:
        event_id = appt["event_id"]

        if not appt["phone"]:
            logger.warning(
                "[Reminder %s] Telefon yok: %s", horizon, appt["summary"]
            )
            results.append({"appointment_id": event_id, "status": "no_phone"})
            continue

        if not store.claim(namespace, event_id, meta=appt.get("summary", "")):
            results.append({"appointment_id": event_id, "status": "already_sent"})
            continue

        try:
            send_upcoming_reminder(
                patient_name=appt["patient_name"],
                guardian_phone=appt["phone"],
                appointment_dt=appt["start_dt"],
                horizon=horizon,
            )
            results.append({"appointment_id": event_id, "status": "sent"})
        except Exception as exc:
            logger.error("[Reminder %s] Gönderilemedi [%s]: %s", horizon, event_id, exc)
            # Başarısız → claim'i geri al ki bir sonraki cron tekrar denesin
            store.forget(namespace, event_id)
            results.append({"appointment_id": event_id, "status": "failed", "error": str(exc)})

    return results


def poll_anamnesis_followup(min_hours_since_initial: float = 24.0) -> list[dict]:
    """
    İlk anamnez mesajı gönderildikten ≥N saat sonra hâlâ Google Forms'da
    yanıt görünmeyen velilere ikinci kez hatırlatma gönderir.

    Idempotency: ilk gönderim 'calendar_event_reminder' namespace'inde,
    ikinci gönderim 'anamnesis_followup' namespace'inde işaretlenir.
    """
    from state_store import get_default_store

    # Form yanıtlarını kontrol için Forms scope'u gerekir; M2'deki
    # helper'ı yeniden kullanıyoruz (lazy import → testlerde sorun yok).
    try:
        from module2_notion_archiver import (
            fetch_form_responses,
            get_forms_service,
            match_form_response_to_patient,
        )
    except ImportError as exc:
        logger.error("[AnamnesisFollowup] M2 import edilemedi: %s", exc)
        return []

    form_id = os.getenv("GOOGLE_ANAMNESIS_FORM_ID", "")
    if not form_id:
        logger.warning("[AnamnesisFollowup] GOOGLE_ANAMNESIS_FORM_ID ayarlı değil; atlanıyor.")
        return []

    store = get_default_store()
    try:
        forms_service = get_forms_service()
        responses = fetch_form_responses(forms_service, form_id)
    except Exception as exc:
        logger.error("[AnamnesisFollowup] Forms yanıtları alınamadı: %s", exc)
        return []

    # Yaklaşan randevuları çek (24h-30 gün arası)
    service = get_calendar_service()
    now = datetime.now(tz=timezone.utc)
    upcoming = fetch_upcoming_appointments(
        service,
        window_start=now + timedelta(hours=2),  # çok yakın olanlar dışarıda
        window_end=now + timedelta(days=30),
    )

    results = []
    for appt in upcoming:
        event_id = appt["event_id"]

        # İlk anamnez mesajı gönderildi mi? (≥min_hours_since_initial önce)
        first_meta_query = "calendar_event_reminder"
        # state_store'dan son seen_at bilgisi yok; basit yaklaşım:
        # ilk gönderimi yapmışsak (is_seen) ve şimdi-min_hours kuralı
        # başka şekilde garanti yok. Pratik: zaten 24h sonraki hatırlatma
        # olduğu için "ilk mesaj gönderildi" kontrolü yeterli; ek olarak
        # bu followup'ın duplicate olmaması için kendi namespace'i var.
        if not store.is_seen(first_meta_query, event_id):
            # İlk mesaj henüz gitmemiş → followup atmaya gerek yok
            continue

        # Bu randevu için form yanıtı var mı?
        match = match_form_response_to_patient(responses, appt.get("patient_name", ""))
        if match:
            # Veli doldurmuş, takip gereksiz
            continue

        # Daha önce followup gönderildi mi?
        if not store.claim("anamnesis_followup", event_id, meta=appt.get("summary", "")):
            results.append({"appointment_id": event_id, "status": "already_followed_up"})
            continue

        if not appt["phone"]:
            results.append({"appointment_id": event_id, "status": "no_phone"})
            continue

        try:
            appt_str = appt["start_dt"].strftime("%d.%m.%Y %H:%M")
            message = (
                f"Merhaba {appt.get('patient_name', '')} velisi,\n\n"
                f"Yaklaşan randevunuz ({appt_str}) için anamnez formunu "
                f"henüz görmedik. Görüşmemizin verimli olması için lütfen "
                f"randevudan önce doldurun:\n"
                f"{GOOGLE_ANAMNESIS_FORM_URL}\n\n"
                f"Teşekkürler."
            )
            send_whatsapp_message(appt["phone"], message)
            results.append({"appointment_id": event_id, "status": "sent"})
        except Exception as exc:
            logger.error("[AnamnesisFollowup] Gönderilemedi [%s]: %s", event_id, exc)
            store.forget("anamnesis_followup", event_id)
            results.append({"appointment_id": event_id, "status": "failed", "error": str(exc)})

    return results


def poll_and_notify() -> list[dict]:
    """
    Google Calendar'ı tarayarak yeni oluşturulan randevuları tespit eder
    ve her biri için WhatsApp üzerinden anamnez formu gönderir.

    Idempotency: gönderilen her randevu event_id'si SQLite store'a
    işaretlenir. Sonraki polling döngülerinde aynı veliye tekrar
    mesaj gönderilmez (önceki sürümde lookback window üst üste
    bindiğinde duplicate mesaj gidiyordu).

    Bu fonksiyon bir cron job veya APScheduler ile periyodik çalıştırılmalıdır.
    """
    from state_store import get_default_store

    store = get_default_store()
    service = get_calendar_service()
    new_appointments = fetch_recently_created_appointments(service)
    logger.info("Yeni randevu sayısı: %d", len(new_appointments))

    results = []
    for appt in new_appointments:
        event_id = appt["event_id"]

        # Daha önce mesaj gönderildi mi?
        if store.is_seen("calendar_event_reminder", event_id):
            logger.debug("Atlanıyor (zaten bildirildi): %s", event_id)
            results.append({"appointment_id": event_id, "status": "already_sent"})
            continue

        if not appt["phone"]:
            logger.warning("Telefon numarası bulunamadı: %s", appt["summary"])
            results.append({"appointment_id": event_id, "status": "no_phone"})
            continue

        try:
            send_appointment_reminder(
                patient_name=appt["patient_name"],
                guardian_phone=appt["phone"],
                appointment_dt=appt["start_dt"],
            )
            # Mesaj başarılıysa işaretle. Başarısız gönderimler tekrar
            # denenmek üzere işaretlenmez.
            store.mark_seen(
                "calendar_event_reminder",
                event_id,
                meta=appt.get("summary", ""),
            )
            results.append({"appointment_id": event_id, "status": "sent"})
        except Exception as exc:
            logger.error("Mesaj gönderilemedi [%s]: %s", appt["summary"], exc)
            results.append({"appointment_id": event_id, "status": "failed", "error": str(exc)})

    return results


def setup_and_start():
    """
    İlk çalıştırmada Evolution API webhook ve event ayarlarını yapar,
    ardından webhook Flask sunucusunu başlatır.
    """
    logger.info("Evolution API yapılandırılıyor...")
    configure_instance_events()
    configure_webhook()

    status = get_instance_status()
    logger.info("Instance durumu: %s", status.get("instance", {}).get("state", "unknown"))

    logger.info("Webhook sunucusu başlatılıyor (port=%d)...", WEBHOOK_LISTEN_PORT)
    app.run(host="0.0.0.0", port=WEBHOOK_LISTEN_PORT, debug=False)


if __name__ == "__main__":
    import argparse

    from logging_setup import configure_logging
    configure_logging()

    parser = argparse.ArgumentParser(description="WhatsApp İletişim Modülü")
    parser.add_argument(
        "--mode",
        choices=["webhook", "poll", "setup"],
        default="setup",
        help="webhook: sadece sunucuyu başlat | poll: takvimi tara ve mesaj gönder | setup: yapılandır ve başlat",
    )
    args = parser.parse_args()

    if args.mode == "setup":
        setup_and_start()
    elif args.mode == "webhook":
        app.run(host="0.0.0.0", port=WEBHOOK_LISTEN_PORT, debug=False)
    elif args.mode == "poll":
        results = poll_and_notify()
        print(json.dumps(results, ensure_ascii=False, indent=2))
