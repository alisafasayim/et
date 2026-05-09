"""
Google Calendar Watch (Push Notifications) entegrasyonu.

Polling yerine push: Calendar olay (event create/update/delete)
oluştuğunda Google bizim verdiğimiz HTTPS URL'e POST atar
(X-Goog-* header'ları ile bildirim).

Watch kanalı en fazla 7 gün geçerli — yenilenmesi gerekir. Kanal
ID'leri ve expiration state_store'da tutulur.

Akış:
  1. start_watch()         → Calendar API'ye watch isteği gönder
  2. /webhook/calendar     → Google bildirimi karşıla → poll_and_notify
  3. _watch_renewal_loop() → expiration yaklaşınca yenile
  4. stop_watch()          → manuel durdurma (opsiyonel)

Webhook URL'i HTTPS olmalı; geçerli SSL sertifikası şart.
WEBHOOK_PUBLIC_URL bu yüzden zaten HTTPS olarak ayarlanmalı.
"""

import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("calendar_watch")

CALENDAR_PUSH_ENABLED = os.getenv("CALENDAR_PUSH_ENABLED", "false").lower() in (
    "1", "true", "yes", "on",
)
CALENDAR_REQUIRE_PUSH_TOKEN = os.getenv("CALENDAR_REQUIRE_PUSH_TOKEN", "true").lower() not in (
    "0", "false", "no", "off",
)
WEBHOOK_PUBLIC_URL = os.getenv("WEBHOOK_PUBLIC_URL", "")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
# Push bildirim token'ı (Google bizim verdiğimiz değeri her POST'ta
# X-Goog-Channel-Token header'ında geri gönderir; doğrulamada kullanırız)
CALENDAR_PUSH_TOKEN = os.getenv("CALENDAR_PUSH_TOKEN", "")

# Yenileme: expiration'dan önceki belirli süre içinde yenile
WATCH_RENEWAL_BUFFER_HOURS = 12


# ---------------------------------------------------------------------------
# Watch state — state_store'da saklanır
# ---------------------------------------------------------------------------

_WATCH_NAMESPACE = "calendar_watch"
_WATCH_KEY = "active"


def _save_watch_state(channel_id: str, resource_id: str, expiration_ms: int) -> None:
    from state_store import get_default_store
    meta = json.dumps(
        {
            "channel_id": channel_id,
            "resource_id": resource_id,
            "expiration_ms": expiration_ms,
        }
    )
    store = get_default_store()
    # Önce sil (her start_watch yeni kanal yaratır), sonra ekle
    store.forget(_WATCH_NAMESPACE, _WATCH_KEY)
    store.mark_seen(_WATCH_NAMESPACE, _WATCH_KEY, meta=meta)


def _load_watch_state() -> dict | None:
    from state_store import get_default_store
    store = get_default_store()
    with store._cursor() as cur:
        cur.execute(
            "SELECT meta FROM processed WHERE namespace=? AND key=?",
            (_WATCH_NAMESPACE, _WATCH_KEY),
        )
        row = cur.fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Watch lifecycle
# ---------------------------------------------------------------------------

def start_watch() -> dict | None:
    """
    Yeni bir watch kanalı oluşturur. CALENDAR_PUSH_ENABLED=false ise
    no-op. WEBHOOK_PUBLIC_URL HTTPS olmalı; aksi halde Google red eder.
    """
    if not CALENDAR_PUSH_ENABLED:
        logger.debug("Calendar push devre dışı (CALENDAR_PUSH_ENABLED=false).")
        return None
    if not WEBHOOK_PUBLIC_URL.startswith("https://"):
        logger.error(
            "Calendar Watch HTTPS gerektirir; WEBHOOK_PUBLIC_URL geçersiz: %s",
            WEBHOOK_PUBLIC_URL,
        )
        return None

    from module3_whatsapp_communicator import get_calendar_service

    service = get_calendar_service()
    channel_id = secrets.token_urlsafe(16)
    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": f"{WEBHOOK_PUBLIC_URL.rstrip('/')}/webhook/calendar",
        "params": {"ttl": str(7 * 24 * 60 * 60)},  # 7 gün
    }
    if CALENDAR_PUSH_TOKEN:
        body["token"] = CALENDAR_PUSH_TOKEN

    response = service.events().watch(calendarId=GOOGLE_CALENDAR_ID, body=body).execute()
    expiration_ms = int(response.get("expiration", 0))
    resource_id = response.get("resourceId", "")

    _save_watch_state(channel_id, resource_id, expiration_ms)
    logger.info(
        "Calendar Watch başlatıldı | channel_id=%s | expiration=%s",
        channel_id,
        datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc).isoformat()
        if expiration_ms else "?",
    )
    return response


def stop_watch() -> bool:
    """Mevcut watch kanalını durdurur."""
    state = _load_watch_state()
    if not state:
        logger.info("Durdurulacak aktif watch yok.")
        return False

    from module3_whatsapp_communicator import get_calendar_service
    service = get_calendar_service()
    try:
        service.channels().stop(
            body={"id": state["channel_id"], "resourceId": state["resource_id"]}
        ).execute()
    except Exception as exc:
        logger.warning("Watch stop hatası (yine de state temizleniyor): %s", exc)

    from state_store import get_default_store
    get_default_store().forget(_WATCH_NAMESPACE, _WATCH_KEY)
    return True


def renewal_needed(state: dict) -> bool:
    """Expiration buffer içine girdi mi?"""
    expiration_ms = state.get("expiration_ms", 0)
    if not expiration_ms:
        return True
    expiration = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    return (expiration - now) < timedelta(hours=WATCH_RENEWAL_BUFFER_HOURS)


def renew_if_needed() -> bool:
    """
    Aktif watch yoksa veya expiration yaklaşıyorsa yeniden başlatır.
    Döner: True yenileme yapıldı / False gerek yoktu.
    """
    state = _load_watch_state()
    if state and not renewal_needed(state):
        return False
    if state:
        try:
            stop_watch()
        except Exception as exc:
            logger.warning("Eski kanal kapatma hatası: %s", exc)
    start_watch()
    return True


def watch_renewal_loop(check_interval_sec: int = 3600) -> None:
    """
    Sonsuz döngü: renewal_buffer içine girdiğinde watch'ı yeniler.
    main.py supervisor thread'i tarafından çağrılır.
    """
    if not CALENDAR_PUSH_ENABLED:
        logger.info("Calendar push devre dışı; renewal loop bypass.")
        return
    logger.info(
        "[CalendarWatch] Renewal loop başlatıldı (kontrol aralığı: %ds)",
        check_interval_sec,
    )
    while True:
        try:
            if renew_if_needed():
                logger.info("[CalendarWatch] Watch yenilendi.")
        except Exception as exc:
            logger.error("[CalendarWatch] Renewal hatası: %s", exc)
        time.sleep(check_interval_sec)


# ---------------------------------------------------------------------------
# Webhook handler — Flask blueprint olarak değil, M3'te route olarak
# eklenir; Google'ın gönderdiği X-Goog-* header'larını işler.
# ---------------------------------------------------------------------------

def verify_push_token(received_token: str) -> bool:
    """
    Google her bildirimde X-Goog-Channel-Token header'ında bizim
    start_watch'ta gönderdiğimiz token'ı geri yollar. CALENDAR_PUSH_TOKEN
    set edilmişse bu kontrol fail-closed çalışır.
    """
    if not CALENDAR_PUSH_TOKEN:
        if CALENDAR_REQUIRE_PUSH_TOKEN:
            logger.critical(
                "CALENDAR_PUSH_TOKEN is not configured; rejecting calendar push fail-closed. "
                "Set CALENDAR_REQUIRE_PUSH_TOKEN=false only for local development."
            )
            return False
        logger.warning("CALENDAR_REQUIRE_PUSH_TOKEN=false; skipping push token check (dev only).")
        return True
    return received_token == CALENDAR_PUSH_TOKEN


def handle_push_notification(headers: dict) -> dict:
    """
    Google Calendar push bildirimi alındığında çağrılır.
    Body genellikle boş; bilgi header'larda:
      - X-Goog-Channel-ID
      - X-Goog-Resource-State (sync | exists | not_exists)
      - X-Goog-Resource-ID
      - X-Goog-Channel-Token

    Yapılan iş: poll_and_notify çağrısı (tetikleme; Calendar push
    payload göndermez, biz yeni randevuyu kendimiz çekeriz).
    """
    state = headers.get("X-Goog-Resource-State", "")
    if state == "sync":
        # İlk handshake; aksiyon gerekmez
        return {"status": "synced"}

    try:
        from module3_whatsapp_communicator import poll_and_notify
        results = poll_and_notify()
    except Exception as exc:
        logger.error("Push tetikli poll_and_notify hatası: %s", exc)
        return {"status": "error", "error": str(exc)}

    sent = sum(1 for r in results if r.get("status") == "sent")
    return {"status": "ok", "state": state, "sent": sent}
