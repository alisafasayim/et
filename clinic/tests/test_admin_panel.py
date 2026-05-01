"""Admin paneli endpoint'leri."""

from unittest.mock import patch

import pytest

pytest.importorskip("flask", reason="Flask yüklü değil")
pytest.importorskip("google.oauth2.credentials", reason="google-auth yüklü değil")


@pytest.fixture
def admin_client(monkeypatch):
    """Admin token set edilmiş bir Flask test client'ı."""
    monkeypatch.setenv("ADMIN_TOKEN", "secret-admin")
    monkeypatch.setenv("WEBHOOK_SECRET", "wa-secret")

    import importlib
    import admin_panel
    import module3_whatsapp_communicator as m3
    importlib.reload(m3)
    importlib.reload(admin_panel)
    admin_panel.register(m3.app)
    m3.app.testing = True
    return m3.app.test_client()


@pytest.fixture
def disabled_admin_client(monkeypatch):
    """ADMIN_TOKEN set edilmemiş — endpoint'ler 404."""
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)

    import importlib
    import admin_panel
    import module3_whatsapp_communicator as m3
    importlib.reload(m3)
    importlib.reload(admin_panel)
    admin_panel.register(m3.app)
    m3.app.testing = True
    return m3.app.test_client()


def _auth(token: str = "secret-admin") -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_disabled_panel_returns_404(disabled_admin_client):
    resp = disabled_admin_client.get("/admin/health")
    assert resp.status_code == 404


def test_health_requires_auth(admin_client):
    resp = admin_client.get("/admin/health")
    assert resp.status_code == 401


def test_health_accepts_bearer_token(admin_client):
    with patch("module3_whatsapp_communicator.get_instance_status",
               return_value={"instance": {"state": "open"}}):
        resp = admin_client.get("/admin/health", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["services"]["whatsapp"]["state"] == "open"


def test_health_accepts_query_token(admin_client):
    with patch("module3_whatsapp_communicator.get_instance_status",
               return_value={"instance": {"state": "open"}}):
        resp = admin_client.get("/admin/health?token=secret-admin")
    assert resp.status_code == 200


def test_health_rejects_wrong_token(admin_client):
    resp = admin_client.get("/admin/health", headers=_auth("wrong"))
    assert resp.status_code == 401


def test_state_summary_returns_namespace_counts(admin_client, tmp_path, monkeypatch):
    import state_store
    fresh = state_store.StateStore(tmp_path / "admin.db")
    fresh.mark_seen("ns_a", "k1")
    fresh.mark_seen("ns_a", "k2")
    fresh.mark_seen("ns_b", "k1")
    monkeypatch.setattr(state_store, "_default_store", fresh)

    resp = admin_client.get("/admin/state/summary", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ns_a"] == 2
    assert body["ns_b"] == 1


def test_state_forget_removes_key(admin_client, tmp_path, monkeypatch):
    import state_store
    fresh = state_store.StateStore(tmp_path / "admin.db")
    fresh.mark_seen("ns", "k")
    monkeypatch.setattr(state_store, "_default_store", fresh)

    resp = admin_client.post(
        "/admin/state/forget",
        json={"namespace": "ns", "key": "k"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert fresh.is_seen("ns", "k") is False


def test_trigger_reminder_invalid_horizon(admin_client):
    resp = admin_client.post("/admin/trigger/reminder?horizon=bogus", headers=_auth())
    assert resp.status_code == 400


def test_trigger_esmm_validates_required_fields(admin_client):
    resp = admin_client.post("/admin/trigger/esmm", json={}, headers=_auth())
    assert resp.status_code == 400


def test_trigger_esmm_calls_main(admin_client):
    payload = {
        "patient_name": "Ali",
        "guardian_phone": "5321234567",
        "tax_id": "12345678901",
        "amount": "1500.00",
    }
    with patch("main.trigger_esmm", return_value={"status": "done"}) as mock_t:
        resp = admin_client.post("/admin/trigger/esmm", json=payload, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "done"
    mock_t.assert_called_once()
