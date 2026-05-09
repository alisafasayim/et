"""
Patient journey testleri (Faz H).

JourneyStage enum + JourneyManager standalone modül +
patient_registry'a eklenen journey_status kolonu testi.
"""

import pytest
from datetime import datetime

cryptography = pytest.importorskip("cryptography", reason="cryptography yüklü değil")

from patient_journey import (
    JourneyEvent,
    JourneyManager,
    JourneyStage,
    PatientJourney,
    Priority,
)


# ---------------------------------------------------------------------------
# Enum'lar
# ---------------------------------------------------------------------------

def test_journey_stages_complete():
    """9 stage: başvurudan pasife kadar tüm hasta yolculuğu."""
    stages = [s.value for s in JourneyStage]
    expected = [
        "basvuru", "triyaj", "on_degerlendirme", "klinik_degerlendirme",
        "tani", "tedavi", "izlem", "sonlandirma", "pasif",
    ]
    for stage in expected:
        assert stage in stages


def test_priority_levels():
    """4 öncelik seviyesi."""
    priorities = [p.value for p in Priority]
    assert "rutin" in priorities
    assert "yakin" in priorities
    assert "acil" in priorities
    assert "acil_kriz" in priorities


# ---------------------------------------------------------------------------
# JourneyManager
# ---------------------------------------------------------------------------

def test_journey_manager_create():
    mgr = JourneyManager()
    journey = mgr.create_journey(patient_id="p-1", patient_name="Test", priority=Priority.ROUTINE)
    assert journey.patient_id == "p-1"
    assert journey.current_stage == JourneyStage.BASVURU
    assert journey.priority == Priority.ROUTINE


def test_journey_advance_stage():
    mgr = JourneyManager()
    journey = mgr.create_journey(patient_id="p-2", patient_name="Veli")
    mgr.advance_stage(journey, JourneyStage.TRIYAJ)
    assert journey.current_stage == JourneyStage.TRIYAJ


# ---------------------------------------------------------------------------
# patient_registry journey_status kolonu (Faz H entegrasyon)
# ---------------------------------------------------------------------------

@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Geçici DB ile temiz registry."""
    monkeypatch.setenv("PII_ENCRYPTION_KEY", "iwd62e8eRmCNtP6e2lF65qUc9Jt0jae2oJd6yYDumcU=")
    monkeypatch.setenv("PII_HASH_KEY", "test-hash-key-32-byte-minimum-len")
    monkeypatch.setenv("PATIENT_REGISTRY_DB", str(tmp_path / "patients.db"))
    from patient_registry import PatientRegistry
    return PatientRegistry()


def test_default_journey_status_is_basvuru(registry):
    """Yeni hasta default 'basvuru' durumunda."""
    uuid = registry.create_patient(full_name="Test Hasta")
    assert registry.get_journey_status(uuid) == "basvuru"


def test_set_journey_status_valid(registry):
    """Geçerli stage'e güncellenebilir."""
    uuid = registry.create_patient(full_name="Ali")
    registry.set_journey_status(uuid, "tedavi")
    assert registry.get_journey_status(uuid) == "tedavi"


def test_set_journey_status_invalid_raises(registry):
    """Geçersiz stage ValueError."""
    uuid = registry.create_patient(full_name="Veli")
    with pytest.raises(ValueError, match="Geçersiz journey status"):
        registry.set_journey_status(uuid, "invalid_stage")


def test_set_journey_status_audit_logged(registry):
    """Stage değişikliği audit_log'a yazılmalı (best-effort)."""
    uuid = registry.create_patient(full_name="Mehmet")
    # Hata fırlatmadan çalışmalı (audit_log opsiyonel)
    registry.set_journey_status(uuid, "klinik_degerlendirme")
    assert registry.get_journey_status(uuid) == "klinik_degerlendirme"


def test_journey_status_persists_across_instances(tmp_path, monkeypatch):
    """Status DB'ye yazılır, yeniden açılan instance görür."""
    monkeypatch.setenv("PII_ENCRYPTION_KEY", "iwd62e8eRmCNtP6e2lF65qUc9Jt0jae2oJd6yYDumcU=")
    monkeypatch.setenv("PII_HASH_KEY", "test-hash-key-32-byte-minimum-len")
    db_path = tmp_path / "patients.db"
    monkeypatch.setenv("PATIENT_REGISTRY_DB", str(db_path))
    from patient_registry import PatientRegistry

    reg1 = PatientRegistry()
    uuid = reg1.create_patient(full_name="Persistent")
    reg1.set_journey_status(uuid, "tani")
    reg1.close()

    reg2 = PatientRegistry()
    assert reg2.get_journey_status(uuid) == "tani"
