"""
Klinik Yönetim Sistemi — Ana Orkestratör

Tüm modülleri tek bir süreç altında birleştirir.

Mimari:
    Modül 1  →  Ses transkripsiyon + SOAP üretimi       (cron: sürekli)
    Modül 2  →  Notion arşivleme                        (Modül 1 tetikler)
    Modül 3  →  WhatsApp bildirim + webhook dinleyici   (arka plan sunucu)
    Modül 4  →  Paraşüt e-SMM üretimi                  (webhook tetikler)
    Modül 5  →  Samsung Notes tek seferlik migrasyon    (manuel --migrate)

Çalıştırma:
    # Normal klinik döngüsü (Modül 1-2-3-4 entegre)
    python main.py

    # İlk kurulum + webhook sunucusu
    python main.py --setup

    # Samsung Notes migrasyonu (tek seferlik)
    python main.py --migrate --dir ./samsung_notes

    # Yalnızca webhook sunucusunu başlat
    python main.py --webhook-only
"""

import argparse
import logging
import os
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Modül içe aktarımları
# ---------------------------------------------------------------------------

from module1_transcription_engine import (
    AUDIO_INBOX_DIR,
    get_calendar_service as get_calendar_service_m1,
    process_audio_file,
)
from module2_notion_archiver import (
    archive_patient_session,
    fetch_form_responses,
    get_forms_service,
)
from module3_whatsapp_communicator import (
    app as flask_app,
    configure_instance_events,
    configure_webhook,
    get_instance_status,
    poll_and_notify,
)
from module4_esmm_generator import (
    CollectionRecord,
    process_collection,
)
from module5_migration import migrate_directory

# ---------------------------------------------------------------------------
# Loglama
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("clinic.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Çevre değişkenleri
# ---------------------------------------------------------------------------

GOOGLE_ANAMNESIS_FORM_ID = os.getenv("GOOGLE_ANAMNESIS_FORM_ID", "")
AUDIO_POLL_INTERVAL_SEC = int(os.getenv("AUDIO_POLL_INTERVAL_SEC", "60"))
CALENDAR_POLL_INTERVAL_SEC = int(os.getenv("CALENDAR_POLL_INTERVAL_SEC", "600"))
WEBHOOK_LISTEN_PORT = int(os.getenv("WEBHOOK_LISTEN_PORT", "5055"))

# ---------------------------------------------------------------------------
# İş Parçacığı 1: Ses Kutusu İzleyici (Modül 1 + 2)
# ---------------------------------------------------------------------------

def _audio_inbox_loop():
    """
    AUDIO_INBOX_DIR klasörünü periyodik tarar.
    Yeni ses dosyası bulunursa:
      1. Modül 1 → transkripsiyon + SOAP JSON
      2. Modül 2 → Notion'a arşivle
    """
    logger.info("[AudioLoop] Başlatıldı | klasör: %s | aralık: %ds",
                AUDIO_INBOX_DIR, AUDIO_POLL_INTERVAL_SEC)

    calendar_service = get_calendar_service_m1()
    forms_service = get_forms_service() if GOOGLE_ANAMNESIS_FORM_ID else None
    all_form_responses: list[dict] = []

    # Form yanıtlarını başlangıçta bir kez çek
    if forms_service and GOOGLE_ANAMNESIS_FORM_ID:
        try:
            all_form_responses = fetch_form_responses(forms_service, GOOGLE_ANAMNESIS_FORM_ID)
            logger.info("[AudioLoop] %d form yanıtı yüklendi", len(all_form_responses))
        except Exception as exc:
            logger.warning("[AudioLoop] Form yanıtları alınamadı: %s", exc)

    processed_dir = AUDIO_INBOX_DIR.parent / "audio_processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    AUDIO_INBOX_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        audio_files = sorted(
            list(AUDIO_INBOX_DIR.glob("*.m4a")) + list(AUDIO_INBOX_DIR.glob("*.mp3"))
        )

        for audio_file in audio_files:
            try:
                logger.info("[AudioLoop] İşleniyor: %s", audio_file.name)

                # Modül 1: Transkripsiyon + SOAP
                soap_notes = process_audio_file(audio_file, calendar_service)

                # Modül 2: Her SOAP notu için Notion arşivi
                for soap_note in soap_notes:
                    try:
                        archive_patient_session(
                            soap_note=soap_note,
                            form_id=GOOGLE_ANAMNESIS_FORM_ID,
                            all_form_responses=all_form_responses,
                        )
                    except Exception as exc:
                        logger.error("[AudioLoop] Notion arşiv hatası [%s]: %s",
                                     soap_note.get("patient_name"), exc)

                # İşlenen dosyayı taşı
                audio_file.rename(processed_dir / audio_file.name)

            except Exception as exc:
                logger.error("[AudioLoop] İşleme hatası [%s]: %s", audio_file.name, exc)

        time.sleep(AUDIO_POLL_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# İş Parçacığı 2: Takvim İzleyici (Modül 3)
# ---------------------------------------------------------------------------

def _calendar_poll_loop():
    """
    Google Calendar'ı periyodik tarar.
    Yeni randevu bulunursa Modül 3 → WhatsApp anamnez formu gönderir.
    """
    logger.info("[CalendarLoop] Başlatıldı | aralık: %ds", CALENDAR_POLL_INTERVAL_SEC)
    while True:
        try:
            results = poll_and_notify()
            sent = sum(1 for r in results if r.get("status") == "sent")
            if sent:
                logger.info("[CalendarLoop] %d yeni randevuya mesaj gönderildi", sent)
        except Exception as exc:
            logger.error("[CalendarLoop] Hata: %s", exc)
        time.sleep(CALENDAR_POLL_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# İş Parçacığı 3: Webhook Sunucusu (Modül 3 + tetikleyici)
# ---------------------------------------------------------------------------

def _webhook_server():
    """
    Evolution API webhook'unu dinler.
    İptal/erteleme mesajlarını işler.
    """
    logger.info("[Webhook] Flask sunucusu başlatılıyor (port=%d)", WEBHOOK_LISTEN_PORT)
    flask_app.run(host="0.0.0.0", port=WEBHOOK_LISTEN_PORT, debug=False, use_reloader=False)


# ---------------------------------------------------------------------------
# e-SMM Tetikleyici (Modül 4) — dışarıdan çağrılır
# ---------------------------------------------------------------------------

def trigger_esmm(
    patient_name: str,
    guardian_phone: str,
    tax_id: str,
    amount: float,
    description: str = "Çocuk ve Ergen Psikiyatrisi Muayenesi",
    appointment_date: str = "",
) -> dict:
    """
    Tahsilat sonrası çağrılan fonksiyon.
    Modül 4'ü tetikler: e-SMM kes → PDF çek → WhatsApp ile ilet.

    Örnek kullanım (dışarıdan):
        from main import trigger_esmm
        trigger_esmm("Ahmet Yılmaz", "05321234567", "12345678901", 1500.0)
    """
    record = CollectionRecord(
        patient_name=patient_name,
        guardian_phone=guardian_phone,
        tax_id=tax_id,
        amount=amount,
        description=description,
        appointment_date=appointment_date,
    )
    return process_collection(record)


# ---------------------------------------------------------------------------
# Kurulum Kontrolü
# ---------------------------------------------------------------------------

def run_setup():
    """Evolution API webhook ve event konfigürasyonunu yapar, bağlantıyı doğrular."""
    logger.info("=== İlk Kurulum ===")
    try:
        configure_instance_events()
        configure_webhook()
        status = get_instance_status()
        state = status.get("instance", {}).get("state", "unknown")
        logger.info("WhatsApp bağlantı durumu: %s", state)
        if state != "open":
            logger.warning("WhatsApp bağlı değil. Evolution API panelinden QR taratın.")
    except Exception as exc:
        logger.error("Kurulum hatası: %s", exc)


# ---------------------------------------------------------------------------
# Ana Başlatıcı
# ---------------------------------------------------------------------------

def start_clinic_system():
    """
    Tüm arka plan iş parçacıklarını başlatır ve ana süreç olarak bekler.
    Ctrl+C ile durdurulabilir.
    """
    logger.info("=" * 60)
    logger.info("Klinik Yönetim Sistemi başlatılıyor...")
    logger.info("=" * 60)

    threads = [
        threading.Thread(target=_audio_inbox_loop, name="AudioLoop", daemon=True),
        threading.Thread(target=_calendar_poll_loop, name="CalendarLoop", daemon=True),
        threading.Thread(target=_webhook_server, name="WebhookServer", daemon=True),
    ]

    for t in threads:
        t.start()
        logger.info("İş parçacığı başlatıldı: %s", t.name)

    logger.info("Sistem çalışıyor. Durdurmak için Ctrl+C.")
    try:
        while True:
            # Tüm iş parçacıklarının sağlığını kontrol et
            for t in threads:
                if not t.is_alive():
                    logger.error("İş parçacığı durdu: %s — yeniden başlatılıyor", t.name)
                    t.start()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Sistem durduruldu.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Klinik Yönetim Sistemi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modlar:
  (varsayılan)    Tüm sistemi başlat (ses izleyici + takvim + webhook)
  --setup         Evolution API yapılandır, ardından sistemi başlat
  --webhook-only  Yalnızca WhatsApp webhook sunucusunu başlat
  --migrate       Samsung Notes → Notion toplu migrasyonu çalıştır

Örnekler:
  python main.py
  python main.py --setup
  python main.py --migrate --dir ./samsung_notes --ext md
  python main.py --webhook-only
        """,
    )
    parser.add_argument("--setup", action="store_true",
                        help="Evolution API ilk kurulumunu yap")
    parser.add_argument("--webhook-only", action="store_true",
                        help="Yalnızca webhook sunucusunu çalıştır")
    parser.add_argument("--migrate", action="store_true",
                        help="Samsung Notes migrasyonunu çalıştır")
    parser.add_argument("--dir", type=Path, default=Path("./samsung_notes"),
                        help="Migrasyon kaynak klasörü")
    parser.add_argument("--ext", choices=["docx", "md", "both"], default="both",
                        help="Migrasyon dosya uzantısı")
    parser.add_argument("--dry-run", action="store_true",
                        help="Migrasyonu simüle et (Notion'a yazma)")
    args = parser.parse_args()

    if args.migrate:
        ext = ["docx", "md"] if args.ext == "both" else [args.ext]
        migrate_directory(source_dir=args.dir, extensions=ext, dry_run=args.dry_run)

    elif args.webhook_only:
        _webhook_server()

    else:
        if args.setup:
            run_setup()
        start_clinic_system()
