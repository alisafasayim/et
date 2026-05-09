"""
Dashboard için iş hacmi istatistikleri.

Yerel SQLite tablolarından (patient_registry, state_store, audit_log)
özet sayıları üretir. Tüm sorgular yerel — Notion/Paraşüt API'sine
gitmez. Dashboard render zamanında çağrılır, ortalama < 50 ms.

Tüm tarih hesaplamaları CLINIC_TZ (varsayılan Europe/Istanbul) yerel
saati üzerinden yapılır. "Bugün" = klinik takvimine göre 00:00–23:59.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("dashboard_stats")

CLINIC_TZ_NAME = os.getenv("CLINIC_TZ", "Europe/Istanbul")

try:
    from zoneinfo import ZoneInfo
    CLINIC_TZ = ZoneInfo(CLINIC_TZ_NAME)
except Exception:
    from datetime import timezone
    CLINIC_TZ = timezone.utc
    logger.warning("CLINIC_TZ '%s' yüklenemedi, UTC kullanılıyor", CLINIC_TZ_NAME)


@dataclass
class DashboardStats:
    """Dashboard'da gösterilecek özet metrikler."""
    patients_today: int = 0
    patients_this_week: int = 0
    patients_this_month: int = 0
    patients_total: int = 0

    consents_active: int = 0  # Şu an açık rızası olan hasta sayısı

    esmm_today: int = 0
    esmm_this_week: int = 0
    esmm_this_month: int = 0

    audit_events_total: int = 0
    audit_events_today: int = 0

    last_patient_at: Optional[str] = None  # ISO string


def _local_now() -> datetime:
    """Klinik yerel saati."""
    return datetime.now(CLINIC_TZ)


def _start_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_week(dt: datetime) -> datetime:
    """Pazartesi 00:00 (ISO 8601: hafta pazartesi başlar)."""
    return _start_of_day(dt - timedelta(days=dt.weekday()))


def _start_of_month(dt: datetime) -> datetime:
    return _start_of_day(dt.replace(day=1))


def _to_utc_iso(dt: datetime) -> str:
    """SQLite'taki created_at ISO formatına çevir (UTC, datetime('now') eşdeğeri)."""
    from datetime import timezone as _tz
    return dt.astimezone(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")


def compute_summary(
    *,
    registry=None,
    state_store=None,
    audit_log=None,
) -> DashboardStats:
    """
    Tüm istatistikleri tek seferde hesapla.

    Bağımlılıklar opsiyonel — None ise lazy default'lara düşer.
    Test'ler için her birini ayrı ayrı mock edebilirsiniz.
    """
    stats = DashboardStats()

    now = _local_now()
    today_start = _to_utc_iso(_start_of_day(now))
    week_start = _to_utc_iso(_start_of_week(now))
    month_start = _to_utc_iso(_start_of_month(now))

    # --- Hastalar ---
    try:
        if registry is None:
            from patient_registry import get_default_registry
            registry = get_default_registry()

        with registry._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM patients WHERE created_at >= ?",
                (today_start,),
            )
            stats.patients_today = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM patients WHERE created_at >= ?",
                (week_start,),
            )
            stats.patients_this_week = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM patients WHERE created_at >= ?",
                (month_start,),
            )
            stats.patients_this_month = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM patients")
            stats.patients_total = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM patients WHERE consent_at IS NOT NULL"
            )
            stats.consents_active = cur.fetchone()[0]

            cur.execute(
                "SELECT MAX(created_at) FROM patients"
            )
            row = cur.fetchone()
            stats.last_patient_at = row[0] if row and row[0] else None
    except Exception as exc:
        logger.warning("Hasta istatistikleri alınamadı: %s", exc)

    # --- e-SMM (state_store'da 'esmm' namespace'inde) ---
    try:
        if state_store is None:
            from state_store import get_default_store
            state_store = get_default_store()

        with state_store._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM processed "
                "WHERE namespace = 'esmm' AND seen_at >= ?",
                (today_start,),
            )
            stats.esmm_today = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM processed "
                "WHERE namespace = 'esmm' AND seen_at >= ?",
                (week_start,),
            )
            stats.esmm_this_week = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM processed "
                "WHERE namespace = 'esmm' AND seen_at >= ?",
                (month_start,),
            )
            stats.esmm_this_month = cur.fetchone()[0]
    except Exception as exc:
        logger.warning("e-SMM istatistikleri alınamadı: %s", exc)

    # --- Audit ---
    try:
        if audit_log is None:
            from audit_log import get_default_audit_log
            audit_log = get_default_audit_log()

        with audit_log._cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit_events")
            stats.audit_events_total = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM audit_events WHERE ts >= ?",
                (today_start,),
            )
            stats.audit_events_today = cur.fetchone()[0]
    except Exception as exc:
        logger.warning("Audit istatistikleri alınamadı: %s", exc)

    return stats
