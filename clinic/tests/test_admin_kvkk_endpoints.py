"""Admin paneli KVKK endpoint'leri: hasta CRUD, onam, audit."""

import pytest

cryptography = pytest.importorskip("cryptography", reason="cryptography yüklü değil")
pytest.importorskip("flask", reason="Flask yüklü değil")
pytest.importorskip("google.oauth2.credentials", reason="google-auth yüklü değil")

from cryptography.fernet import Fernet


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-admin")
    monkeypatch.setenv("WEBHOOK_SECRET", "wa-secret")
    monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PII_HASH_KEY", "test-salt")
    monkeypatch.setenv("PATIENT_REGISTRY_DB", str(tmp_path / "reg.db"))
    monkeypatch.setenv("AUDIT_LOG_DB", str(tmp_path / "audit.db"))

    import importlib
    import audit_log
    import patient_registry
    import pii_crypto
    pii_crypto.reset_cache()
    patient_registry.reset_cache()
    audit_log.reset_cache()

    import admin_panel
    import module3_whatsapp_communicator as m3
    importlib.reload(m3)
    importlib.reload(admin_panel)
    admin_panel.register(m3.app)
    m3.app.testing = True
    return m3.app.test_client()


def _auth() -> dict:
    return {"Authorization": "Bearer secret-admin"}


def test_register_patient_returns_uuid_and_pseudonym(admin_client):
    resp = admin_client.post(
        "/admin/patients/register",
        json={"full_name": "Ali Yıldız", "tax_id": "12345678901"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert "uuid" in body
    assert body["pseudonym"].startswith("#")
    assert body["consent_at"] is None


def test_register_with_consent_now(admin_client):
    resp = admin_client.post(
        "/admin/patients/register",
        json={"full_name": "Ali", "consent_now": True},
        headers=_auth(),
    )
    assert resp.get_json()["consent_at"] is not None


def test_register_requires_full_name(admin_client):
    resp = admin_client.post(
        "/admin/patients/register", json={}, headers=_auth()
    )
    assert resp.status_code == 400


def test_lookup_by_name_returns_match(admin_client):
    admin_client.post(
        "/admin/patients/register",
        json={"full_name": "Ayşe Demir"},
        headers=_auth(),
    )
    resp = admin_client.get("/admin/patients/lookup?name=Ayşe Demir", headers=_auth())
    assert resp.status_code == 200
    results = resp.get_json()
    assert len(results) == 1
    assert results[0]["full_name"] == "Ayşe Demir"


def test_lookup_by_tax_id(admin_client):
    admin_client.post(
        "/admin/patients/register",
        json={"full_name": "Test", "tax_id": "98765432109"},
        headers=_auth(),
    )
    resp = admin_client.get(
        "/admin/patients/lookup?tax_id=98765432109", headers=_auth()
    )
    results = resp.get_json()
    assert len(results) == 1


def test_lookup_requires_param(admin_client):
    resp = admin_client.get("/admin/patients/lookup", headers=_auth())
    assert resp.status_code == 400


def test_consent_grant_and_revoke(admin_client):
    reg = admin_client.post(
        "/admin/patients/register",
        json={"full_name": "Test"},
        headers=_auth(),
    )
    uid = reg.get_json()["uuid"]

    grant = admin_client.post(f"/admin/patients/{uid}/consent", headers=_auth())
    assert grant.get_json()["status"] == "granted"

    revoke = admin_client.delete(f"/admin/patients/{uid}/consent", headers=_auth())
    assert revoke.get_json()["status"] == "revoked"


def test_consent_404_for_unknown_patient(admin_client):
    resp = admin_client.post(
        "/admin/patients/00000000-0000-0000-0000-000000000000/consent",
        headers=_auth(),
    )
    assert resp.status_code == 404


def test_delete_patient_unforgettable_audit(admin_client):
    reg = admin_client.post(
        "/admin/patients/register",
        json={"full_name": "Silinecek"},
        headers=_auth(),
    )
    uid = reg.get_json()["uuid"]

    resp = admin_client.delete(f"/admin/patients/{uid}", headers=_auth())
    assert resp.get_json()["status"] == "deleted"

    # İkinci silmede kayıt yok
    resp2 = admin_client.delete(f"/admin/patients/{uid}", headers=_auth())
    assert resp2.get_json()["status"] == "not_found"

    # Audit log'a delete event'i düştü mü
    audit = admin_client.get(
        f"/admin/audit?patient_uuid={uid}&action=patient.delete",
        headers=_auth(),
    )
    rows = audit.get_json()
    assert any(r["action"] == "patient.delete" for r in rows)


def test_audit_endpoint_returns_recent_actions(admin_client):
    admin_client.post(
        "/admin/patients/register",
        json={"full_name": "A"},
        headers=_auth(),
    )
    resp = admin_client.get("/admin/audit?action=patient.create", headers=_auth())
    rows = resp.get_json()
    assert len(rows) >= 1
    assert all(r["action"] == "patient.create" for r in rows)


def test_admin_access_writes_audit(admin_client):
    admin_client.get("/admin/health", headers=_auth())
    resp = admin_client.get("/admin/audit?action=admin.access", headers=_auth())
    rows = resp.get_json()
    # Health çağrısı admin.access olarak audit'e düşmeli
    assert any("/admin/health" in (r["details"] or {}).get("endpoint", "") for r in rows)
