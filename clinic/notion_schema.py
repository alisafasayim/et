"""
Notion DB schema yapılandırması.

İki schema desteklenir:
  - LEGACY (NOTION_EXTENDED_SCHEMA=false, varsayılan): clinic/ orijinal
    tek-DB yapısı. Property: "Hasta Adı" (title), "Randevu Tarihi" (date),
    "Randevu ID" (rich_text), "Durum" (select).
  - EXTENDED (NOTION_EXTENDED_SCHEMA=true): clinic_automation 5-DB schema'sı
    (Hastalar, Konsültasyonlar, Ses Kayıtları, Form Yanıtları, Personel).
    Property: "İsim" (title), "Tarih" (date), "Hasta" (relation), vb.

Bu modül property isimlerini ve DB ID kararlarını TEK YERDE tutar.
Module2 ve testler bu config üzerinden çalışır — string literal kullanmaz.

Yeni bir property gerektiğinde sadece bu dosyada güncelleme yapılır.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Schema mod seçimi
# ---------------------------------------------------------------------------

EXTENDED_SCHEMA = os.getenv("NOTION_EXTENDED_SCHEMA", "false").lower() in (
    "1", "true", "yes", "on",
)


# ---------------------------------------------------------------------------
# Property name haritası
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PatientProps:
    """Hastalar DB'si için property isimleri."""
    title: str = "Hasta Adı"            # Hasta adı/pseudonym (title)
    appointment_date: str = "Randevu Tarihi"  # Sadece LEGACY'de — randevu zamanı
    appointment_id: str = "Randevu ID"   # Sadece LEGACY'de — Calendar event ID
    status: str = "Durum"                # Hasta durumu (Aktif/Arşivlendi/...)
    age: Optional[str] = None            # Sadece EXTENDED'de
    parent_name: Optional[str] = None    # Veli adı
    phone: Optional[str] = None          # Veli telefonu


@dataclass(frozen=True)
class SessionProps:
    """Konsültasyonlar (Sessions) DB'si için property isimleri."""
    title: str = "Başlık"                 # "Seans - YYYY-MM-DD - Hasta"
    patient_relation: str = "Hasta"       # Hastalar DB'sine relation
    date: str = "Tarih"                   # Seans tarihi
    diagnosis: Optional[str] = "Tanı"     # Tanı kodu/metni


@dataclass(frozen=True)
class AudioRecordProps:
    """Ses Kayıtları DB'si için property isimleri."""
    title: str = "Dosya Adı"
    session_relation: str = "Seans"       # Sessions DB'sine relation
    duration: Optional[str] = "Süre"      # rich_text "00:42:30"
    confidence: Optional[str] = "Güven"   # number 0-1


@dataclass(frozen=True)
class FormResponseProps:
    """Form Yanıtları DB'si için property isimleri."""
    title: str = "Başlık"                 # "Anamnez - Hasta"
    patient_relation: str = "Hasta"
    submitted_at: str = "Form Tarihi"


@dataclass(frozen=True)
class StaffProps:
    """Personel DB'si için property isimleri (placeholder)."""
    title: str = "Ad Soyad"
    role: str = "Görev"
    phone: str = "Telefon"


# ---------------------------------------------------------------------------
# DB ID seçimi
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DatabaseIds:
    """
    Hangi DB ID'leri kullanılacak — extended schema'da 5 ayrı DB,
    legacy'de hepsi tek DB (NOTION_DATABASE_ID).
    """
    patients: str
    sessions: Optional[str]
    audio_records: Optional[str]
    form_responses: Optional[str]
    staff: Optional[str]


def _env(name: str, default: str = "") -> str:
    val = os.getenv(name, default)
    return val.strip() if val else default


def get_database_ids() -> DatabaseIds:
    """Env'den DB ID'leri okur, schema mod'una göre fallback uygular."""
    legacy_id = _env("NOTION_DATABASE_ID")

    if EXTENDED_SCHEMA:
        return DatabaseIds(
            patients=_env("NOTION_PATIENTS_DB_ID", legacy_id),
            sessions=_env("NOTION_SESSIONS_DB_ID") or None,
            audio_records=_env("NOTION_AUDIO_RECORDS_DB_ID") or None,
            form_responses=_env("NOTION_FORM_RESPONSES_DB_ID") or None,
            staff=_env("NOTION_STAFF_DB_ID") or None,
        )
    return DatabaseIds(
        patients=legacy_id,
        sessions=None,
        audio_records=None,
        form_responses=None,
        staff=None,
    )


# ---------------------------------------------------------------------------
# Property name seçimi (legacy vs extended)
# ---------------------------------------------------------------------------

LEGACY_PATIENT = PatientProps()  # default = legacy
EXTENDED_PATIENT = PatientProps(
    title="İsim",
    appointment_date="Randevu Tarihi",  # legacy'de bile yararlı
    appointment_id="Randevu ID",
    status="Durum",
    age="Yaş",
    parent_name="Veli Adı",
    phone="Telefon",
)


def patient_props() -> PatientProps:
    return EXTENDED_PATIENT if EXTENDED_SCHEMA else LEGACY_PATIENT


def session_props() -> SessionProps:
    return SessionProps()  # extended schema'ya özel — legacy'de kullanılmaz


def audio_record_props() -> AudioRecordProps:
    return AudioRecordProps()


def form_response_props() -> FormResponseProps:
    return FormResponseProps()


def staff_props() -> StaffProps:
    return StaffProps()


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

def is_extended() -> bool:
    """NOTION_EXTENDED_SCHEMA=true mu?"""
    return EXTENDED_SCHEMA


def has_separate_sessions_db() -> bool:
    """Konsültasyonlar için ayrı DB yapılandırılmış mı?"""
    return EXTENDED_SCHEMA and bool(get_database_ids().sessions)
