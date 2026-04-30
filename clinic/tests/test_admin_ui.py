"""Admin Web UI smoke testleri."""

import pytest

cryptography = pytest.importorskip("cryptography", reason="cryptography yüklü değil")
pytest.importorskip("flask", reason="Flask yüklü değil")
pytest.importorskip("google.oauth2.credentials", reason="google-auth yüklü değil")

from cryptography.fernet import Fernet


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-admin")
    monkeypatch.setenv("WEBHOOK_SECRET", "wa-secret")
    monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PII_HASH_KEY", "test-salt")
    monkeypatch.setenv("PATIENT_REGISTRY_DB", str(tmp_path / "reg.db"))
    monkeypatch.setenv("AUDIT_LOG_DB", str(tmp_path / "audit.db"))
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-flask-secret")

    import importlib
    import audit_log, patient_registry, pii_crypto
    pii_crypto.reset_cache()
    patient_registry.reset_cache()
    audit_log.reset_cache()

    import admin_panel, admin_ui
    import module3_whatsapp_communicator as m3
    importlib.reload(m3)
    importlib.reload(admin_panel)
    importlib.reload(admin_ui)
    admin_panel.register(m3.app)
    admin_ui.register(m3.app)
    m3.app.testing = True
    return m3.app.test_client()


def _login(c):
    """Login formu submit + session cookie set."""
    return c.post("/ui/login", data={"token": "secret-admin"}, follow_redirects=False)


def test_ui_disabled_when_no_admin_token(monkeypatch, tmp_path):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("FLASK_SECRET_KEY", "x")

    import importlib
    import admin_ui
    import module3_whatsapp_communicator as m3
    importlib.reload(m3)
    importlib.reload(admin_ui)
    admin_ui.register(m3.app)
    m3.app.testing = True
    client = m3.app.test_client()

    assert client.get("/ui/login").status_code == 404
    assert client.get("/ui/").status_code == 404


def test_login_page_renders(client):
    resp = client.get("/ui/login")
    assert resp.status_code == 200
    assert b"Klinik Admin" in resp.data
    assert b"token" in resp.data.lower()


def test_protected_pages_redirect_to_login(client):
    resp = client.get("/ui/", follow_redirects=False)
    assert resp.status_code in (302, 308)
    assert "/ui/login" in resp.headers["Location"]


def test_login_with_correct_token_succeeds(client):
    resp = _login(client)
    assert resp.status_code == 302
    # Dashboard'a redirect
    assert "/ui/" in resp.headers["Location"]


def test_login_with_wrong_token_fails(client):
    resp = client.post("/ui/login", data={"token": "wrong"})
    assert resp.status_code == 200  # Login formu tekrar render edilir
    assert b"ge" in resp.data.lower()  # "geçersiz" mesajı (HTML escape'le)


def test_dashboard_renders_after_login(client, monkeypatch):
    _login(client)
    # Health çağrısı WhatsApp'ı çağırır → mock
    from unittest.mock import patch
    with patch(
        "module3_whatsapp_communicator.get_instance_status",
        return_value={"instance": {"state": "open"}},
    ):
        resp = client.get("/ui/")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_patients_list_empty_then_create(client):
    _login(client)
    resp = client.get("/ui/patients")
    assert resp.status_code == 200

    # CSRF token sayfadan al
    import re
    m = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
    assert m, "CSRF token sayfada bulunamadı"
    csrf = m.group(1).decode()

    # Yeni hasta kaydet
    resp = client.post(
        "/ui/patients",
        data={"csrf_token": csrf, "full_name": "Test Hasta", "consent_now": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Detay sayfasına redirect
    assert "/ui/patients/" in resp.headers["Location"]


def test_csrf_required_for_post(client):
    _login(client)
    resp = client.post("/ui/patients", data={"full_name": "Hacker"})
    assert resp.status_code == 403


def test_logout_clears_session(client):
    _login(client)
    resp = client.get("/ui/logout", follow_redirects=False)
    assert resp.status_code == 302
    # Şimdi protected sayfa redirect döner
    resp2 = client.get("/ui/", follow_redirects=False)
    assert "/ui/login" in resp2.headers["Location"]


def test_audit_endpoint_renders(client):
    _login(client)
    resp = client.get("/ui/audit")
    assert resp.status_code == 200
    assert b"Denetim" in resp.data


def test_patient_detail_404_for_unknown(client):
    _login(client)
    resp = client.get("/ui/patients/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
