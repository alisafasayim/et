"""patient_registry: PII şifreli hasta deposu."""

import pytest

cryptography = pytest.importorskip("cryptography", reason="cryptography yüklü değil")
from cryptography.fernet import Fernet


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PII_HASH_KEY", "test-salt")

    import pii_crypto
    pii_crypto.reset_cache()

    from patient_registry import PatientRegistry
    return PatientRegistry(tmp_path / "reg.db")


def test_create_returns_uuid_and_can_be_fetched(registry):
    uid = registry.create_patient(
        full_name="Ali Yıldız",
        tax_id="12345678901",
        phone="05321234567",
        birth_date="2010-05-15",
    )
    assert isinstance(uid, str) and len(uid) >= 32
    rec = registry.get_patient(uid)
    assert rec is not None
    assert rec["full_name"] == "Ali Yıldız"
    assert rec["tax_id"] == "12345678901"
    assert rec["phone"] == "05321234567"
    assert rec["pseudonym"].startswith("#")


def test_pii_is_encrypted_at_rest(registry, tmp_path):
    """DB dosyasında düz metin TCKN olmamalı."""
    uid = registry.create_patient("Mehmet Kaya", tax_id="98765432109")

    # Doğrudan DB dosyasını oku — TCKN orada düz metin geçmemeli
    db_bytes = (tmp_path / "reg.db").read_bytes()
    assert b"98765432109" not in db_bytes
    assert b"Mehmet Kaya" not in db_bytes


def test_find_by_tax_id_returns_existing(registry):
    uid = registry.create_patient("Ayşe Demir", tax_id="11111111111")
    found = registry.find_by_tax_id("11111111111")
    assert found is not None
    assert found["uuid"] == uid


def test_find_by_tax_id_not_found(registry):
    assert registry.find_by_tax_id("99999999999") is None


def test_create_dedup_on_existing_tax_id(registry):
    """Aynı TCKN ile ikinci create → mevcut UUID döner."""
    uid_a = registry.create_patient("Ali Yıldız", tax_id="12345678901")
    uid_b = registry.create_patient("Ali Yıldız", tax_id="12345678901")
    assert uid_a == uid_b


def test_find_by_name_returns_list(registry):
    """Aynı isimli birden fazla hasta olabilir."""
    a = registry.create_patient("Ortak İsim", tax_id="11111111111")
    b = registry.create_patient("Ortak İsim", tax_id="22222222222")
    found = registry.find_by_name("Ortak İsim")
    uuids = {r["uuid"] for r in found}
    assert uuids == {a, b}


def test_find_by_name_case_insensitive(registry):
    uid = registry.create_patient("Mehmet Kaya")
    assert any(r["uuid"] == uid for r in registry.find_by_name("MEHMET kaya"))


def test_attach_notion_page(registry):
    uid = registry.create_patient("Test Hasta")
    registry.attach_notion_page(uid, "notion-page-123")
    rec = registry.get_patient(uid)
    assert rec["notion_page_id"] == "notion-page-123"


def test_consent_lifecycle(registry):
    uid = registry.create_patient("Test")
    rec_before = registry.get_patient(uid)
    assert rec_before["consent_at"] is None

    registry.record_consent(uid)
    rec_after = registry.get_patient(uid)
    assert rec_after["consent_at"] is not None

    registry.revoke_consent(uid)
    rec_revoked = registry.get_patient(uid)
    assert rec_revoked["consent_at"] is None


def test_delete_patient(registry):
    uid = registry.create_patient("Silinecek")
    assert registry.delete_patient(uid) is True
    assert registry.get_patient(uid) is None
    assert registry.delete_patient(uid) is False  # zaten yok


def test_list_all_returns_recent_first(registry):
    a = registry.create_patient("A")
    b = registry.create_patient("B")
    rows = registry.list_all()
    assert len(rows) == 2
    # Son ekleneni ilk gösterir
    assert rows[0]["full_name"] == "B"
    assert rows[1]["full_name"] == "A"


def test_create_requires_full_name(registry):
    with pytest.raises(ValueError):
        registry.create_patient("")
