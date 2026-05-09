"""
notion_schema modülü ve module2 extended-schema yolu testleri.

NOTION_EXTENDED_SCHEMA env'i ile schema seçimi, property
isim haritalaması ve _archive_extended dispatch davranışı
kontrol edilir. Gerçek Notion API'sine istek gitmez (mock).
"""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# notion_schema config testleri
# ---------------------------------------------------------------------------

def _reload_schema():
    """notion_schema'yı env'den yeniden okumak için reimport eder."""
    if "notion_schema" in sys.modules:
        del sys.modules["notion_schema"]
    return importlib.import_module("notion_schema")


def test_legacy_schema_default(monkeypatch):
    """NOTION_EXTENDED_SCHEMA set değilse legacy property'ler kullanılır."""
    monkeypatch.delenv("NOTION_EXTENDED_SCHEMA", raising=False)
    monkeypatch.setenv("NOTION_DATABASE_ID", "legacy-db-id")
    schema = _reload_schema()

    assert schema.is_extended() is False
    assert schema.patient_props().title == "Hasta Adı"
    assert schema.patient_props().status == "Durum"
    assert schema.patient_props().appointment_date == "Randevu Tarihi"

    db_ids = schema.get_database_ids()
    assert db_ids.patients == "legacy-db-id"
    assert db_ids.sessions is None
    assert schema.has_separate_sessions_db() is False


def test_extended_schema_enabled(monkeypatch):
    """NOTION_EXTENDED_SCHEMA=true ile extended property'ler ve 5 DB."""
    monkeypatch.setenv("NOTION_EXTENDED_SCHEMA", "true")
    monkeypatch.setenv("NOTION_PATIENTS_DB_ID", "patients-db")
    monkeypatch.setenv("NOTION_SESSIONS_DB_ID", "sessions-db")
    monkeypatch.setenv("NOTION_AUDIO_RECORDS_DB_ID", "audio-db")
    monkeypatch.setenv("NOTION_FORM_RESPONSES_DB_ID", "forms-db")
    monkeypatch.setenv("NOTION_STAFF_DB_ID", "staff-db")
    schema = _reload_schema()

    assert schema.is_extended() is True
    assert schema.patient_props().title == "İsim"
    assert schema.patient_props().age == "Yaş"
    assert schema.patient_props().parent_name == "Veli Adı"
    assert schema.patient_props().phone == "Telefon"

    db_ids = schema.get_database_ids()
    assert db_ids.patients == "patients-db"
    assert db_ids.sessions == "sessions-db"
    assert db_ids.audio_records == "audio-db"
    assert db_ids.form_responses == "forms-db"
    assert db_ids.staff == "staff-db"
    assert schema.has_separate_sessions_db() is True


def test_session_props_uses_relation_field():
    """Sessions DB property isimleri clinic_automation şemasıyla uyumlu."""
    schema = _reload_schema()
    s = schema.session_props()
    assert s.title == "Başlık"
    assert s.patient_relation == "Hasta"
    assert s.date == "Tarih"


def test_extended_falls_back_to_legacy_db_id(monkeypatch):
    """NOTION_PATIENTS_DB_ID yoksa NOTION_DATABASE_ID fallback edilir."""
    monkeypatch.setenv("NOTION_EXTENDED_SCHEMA", "true")
    monkeypatch.delenv("NOTION_PATIENTS_DB_ID", raising=False)
    monkeypatch.setenv("NOTION_DATABASE_ID", "single-db")
    schema = _reload_schema()

    assert schema.get_database_ids().patients == "single-db"


def test_extended_without_sessions_db_returns_false(monkeypatch):
    """Sessions DB ID yoksa has_separate_sessions_db False döner."""
    monkeypatch.setenv("NOTION_EXTENDED_SCHEMA", "true")
    monkeypatch.setenv("NOTION_PATIENTS_DB_ID", "patients-db")
    monkeypatch.delenv("NOTION_SESSIONS_DB_ID", raising=False)
    schema = _reload_schema()

    assert schema.has_separate_sessions_db() is False


# ---------------------------------------------------------------------------
# create_session_page testi (mock'lu Notion API)
# ---------------------------------------------------------------------------

def test_create_session_page_payload(monkeypatch):
    """create_session_page doğru Sessions DB'sine doğru property'lerle yazar."""
    monkeypatch.setenv("NOTION_EXTENDED_SCHEMA", "true")
    monkeypatch.setenv("NOTION_PATIENTS_DB_ID", "patients-db")
    monkeypatch.setenv("NOTION_SESSIONS_DB_ID", "sessions-db")
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")

    # notion_schema'yı reload (env değişti)
    _reload_schema()
    if "module2_notion_archiver" in sys.modules:
        del sys.modules["module2_notion_archiver"]
    m2 = importlib.import_module("module2_notion_archiver")

    captured: list[tuple[str, dict]] = []

    def fake_post(endpoint: str, payload: dict) -> dict:
        captured.append((endpoint, payload))
        return {"id": "session-page-123"}

    monkeypatch.setattr(m2, "_notion_post", fake_post)

    page_id = m2.create_session_page(
        patient_page_id="patient-root-id",
        patient_name="#a4f9-c2b1",
        session_date="2026-05-01",
        diagnosis="F90.0 DEHB",
    )

    assert page_id == "session-page-123"
    assert len(captured) == 1
    endpoint, payload = captured[0]
    assert endpoint == "/pages"
    assert payload["parent"]["database_id"] == "sessions-db"

    props = payload["properties"]
    assert "Seans - 2026-05-01 - #a4f9-c2b1" in props["Başlık"]["title"][0]["text"]["content"]
    assert props["Hasta"]["relation"][0]["id"] == "patient-root-id"
    assert props["Tarih"]["date"]["start"] == "2026-05-01"
    assert "F90.0" in props["Tanı"]["rich_text"][0]["text"]["content"]


def test_create_session_page_raises_without_extended(monkeypatch):
    """Extended schema kapalıyken create_session_page çağrısı ValueError."""
    monkeypatch.setenv("NOTION_EXTENDED_SCHEMA", "false")
    monkeypatch.delenv("NOTION_SESSIONS_DB_ID", raising=False)
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")

    _reload_schema()
    if "module2_notion_archiver" in sys.modules:
        del sys.modules["module2_notion_archiver"]
    m2 = importlib.import_module("module2_notion_archiver")

    with pytest.raises(ValueError, match="Sessions DB"):
        m2.create_session_page(
            patient_page_id="x", patient_name="y", session_date="2026-05-01"
        )


def test_archive_dispatches_to_extended_when_sessions_db_set(monkeypatch):
    """Sessions DB ayarlıysa archive_patient_session _archive_extended'e gider."""
    monkeypatch.setenv("NOTION_EXTENDED_SCHEMA", "true")
    monkeypatch.setenv("NOTION_PATIENTS_DB_ID", "patients-db")
    monkeypatch.setenv("NOTION_SESSIONS_DB_ID", "sessions-db")
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")
    monkeypatch.setenv("KVKK_HYBRID_MODE", "false")

    _reload_schema()
    if "module2_notion_archiver" in sys.modules:
        del sys.modules["module2_notion_archiver"]
    m2 = importlib.import_module("module2_notion_archiver")

    flat_called = MagicMock(return_value="flat-page")
    extended_called = MagicMock(return_value="extended-page")
    hierarchical_called = MagicMock(return_value="hier-page")

    monkeypatch.setattr(m2, "_archive_flat", flat_called)
    monkeypatch.setattr(m2, "_archive_extended", extended_called)
    monkeypatch.setattr(m2, "_archive_hierarchical", hierarchical_called)

    page_id = m2.archive_patient_session(
        soap_note={"patient_name": "Test", "soap": {}},
        form_id="",
        all_form_responses=[],
    )

    assert page_id == "extended-page"
    extended_called.assert_called_once()
    flat_called.assert_not_called()
    hierarchical_called.assert_not_called()


def test_archive_dispatches_to_flat_when_legacy(monkeypatch):
    """Legacy schema (NOTION_EXTENDED_SCHEMA yok) → _archive_flat çağrılır."""
    monkeypatch.delenv("NOTION_EXTENDED_SCHEMA", raising=False)
    monkeypatch.delenv("NOTION_HIERARCHICAL_MODE", raising=False)
    monkeypatch.setenv("NOTION_DATABASE_ID", "legacy-db")
    monkeypatch.setenv("NOTION_TOKEN", "secret_test")

    _reload_schema()
    if "module2_notion_archiver" in sys.modules:
        del sys.modules["module2_notion_archiver"]
    m2 = importlib.import_module("module2_notion_archiver")

    flat_called = MagicMock(return_value="flat-page")
    monkeypatch.setattr(m2, "_archive_flat", flat_called)

    page_id = m2.archive_patient_session(
        soap_note={"patient_name": "Test", "soap": {}},
        form_id="",
        all_form_responses=[],
    )

    assert page_id == "flat-page"
    flat_called.assert_called_once()
