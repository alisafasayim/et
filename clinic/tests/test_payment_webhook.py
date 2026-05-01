"""POS / payment webhook → e-SMM tetikleme."""

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

pytest.importorskip("flask", reason="Flask yüklü değil")
pytest.importorskip("google.oauth2.credentials", reason="google-auth yüklü değil")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("WEBHOOK_SECRET", "wa-secret")
    monkeypatch.setenv("WEBHOOK_REQUIRE_SIGNATURE", "true")

    import importlib
    import module3_whatsapp_communicator as m3
    importlib.reload(m3)
    m3.app.testing = True
    return m3.app.test_client(), m3


def _sign(body: bytes, secret: str = "test-secret") -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_payment_webhook_rejects_missing_signature(client):
    c, _ = client
    body = json.dumps({"event": "payment.succeeded"}).encode()
    resp = c.post("/webhook/payment", data=body, content_type="application/json")
    assert resp.status_code == 401


def test_payment_webhook_rejects_bad_signature(client):
    c, _ = client
    body = json.dumps({"event": "payment.succeeded"}).encode()
    resp = c.post(
        "/webhook/payment",
        data=body,
        content_type="application/json",
        headers={"X-Webhook-Signature": "bogus"},
    )
    assert resp.status_code == 401


def test_payment_webhook_ignores_non_success_event(client):
    c, _ = client
    body = json.dumps({"event": "payment.failed"}).encode()
    resp = c.post(
        "/webhook/payment",
        data=body,
        content_type="application/json",
        headers={"X-Webhook-Signature": _sign(body)},
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ignored"


def test_payment_webhook_ignores_missing_required_fields(client):
    c, _ = client
    body = json.dumps({"event": "payment.succeeded", "amount": "100"}).encode()
    resp = c.post(
        "/webhook/payment",
        data=body,
        content_type="application/json",
        headers={"X-Webhook-Signature": _sign(body)},
    )
    assert resp.get_json()["status"] == "ignored"


def test_payment_webhook_queues_esmm_on_success(client):
    c, m3 = client
    payload = {
        "event": "payment.succeeded",
        "amount": "1500.00",
        "patient_name": "Test Hasta",
        "guardian_phone": "905321234567",
        "tax_id": "12345678901",
        "collection_key": "pos-tx-1",
    }
    body = json.dumps(payload).encode()

    with patch("main.trigger_esmm") as trigger_mock:
        trigger_mock.return_value = {"status": "done"}
        resp = c.post(
            "/webhook/payment",
            data=body,
            content_type="application/json",
            headers={"X-Webhook-Signature": _sign(body)},
        )
        # Bekle: webhook 202 döner, background thread trigger_esmm'i çağırır
        # Background thread için bir nebze bekle
        import time
        for _ in range(20):
            if trigger_mock.called:
                break
            time.sleep(0.05)

    assert resp.status_code == 202
    assert resp.get_json()["status"] == "queued"
    trigger_mock.assert_called_once()
    kwargs = trigger_mock.call_args.kwargs
    assert kwargs["patient_name"] == "Test Hasta"
    assert kwargs["amount"] == "1500.00"
    assert kwargs["collection_key"] == "pos-tx-1"


def test_normalize_accepts_alternative_status_field(client):
    _, m3 = client
    normalized = m3._normalize_payment_payload(
        {
            "status": "paid",
            "amount": "500",
            "patient_name": "Ali",
            "guardian_phone": "5551234567",
            "tax_id": "12345678901",
        }
    )
    assert normalized is not None
    assert normalized["amount"] == "500"
