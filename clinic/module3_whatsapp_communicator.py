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
GOOGLE_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")

# Yeni randevu taraması için kaç dakika öncesini kontrol et
CALENDAR_POLL_LOOKBACK_MINUTES = int(os.getenv("CALENDAR_POLL_LOOKBACK_MINUTES", "10"))

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
    Randevu hatırlatma ve anamnez formu mesajını gönderir.
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


def handle_cancellation_request(sender_phone: str, message_text: str) -> None:
    """
    İptal talebini loglar ve doktora bildirim mesajı gönderir.
    """
    doctor_phone = os.getenv("DOCTOR_PHONE", "")
    logger.warning("İPTAL TALEBİ | Gönderen: %s | Mesaj: %s", sender_phone, message_text)

    # Gönderene otomatik yanıt
    send_whatsapp_message(
        sender_phone,
        "İptal talebiniz alındı. En kısa sürede sizinle iletişime geçeceğiz.",
    )

    # Doktora bildirim
    if doctor_phone:
        send_whatsapp_message(
            doctor_phone,
            f"⚠️ İptal Talebi\nNumara: +{_normalize_phone(sender_phone)}\nMesaj: {message_text}",
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
