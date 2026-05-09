"""POS / payment webhook → e-SMM tetikleme."""

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest

cryptography = pytest.importorskip("cryptography", reason="cryptography not installed")
from cryptography.fernet import Fernet

pytest.importorskip("flask", reason="Flask yüklü değil")
pytest.importorskip("google.oauth2.credentials", reason="google-auth yüklü değil")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("WEBHOOK_SECRET", "wa-secret")
    monkeypatch.setenv("WEBHOOK_REQUIRE_SIGNATURE", "true")
    monkeypatch.setenv("CLINIC_STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PII_HASH_KEY", "test-hash-salt")

    import importlib
    import pii_crypto
    import state_store
    pii_crypto.reset_cache()
    importlib.reload(state_store)
    state_store.reset_cache()
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


def test_payment_webhook_persists_job_before_worker_starts(client):
    c, m3 = client
    payload = {
        "event": "payment.succeeded",
        "amount": "1500.00",
        "patient_name": "Test Hasta",
        "guardian_phone": "905321234567",
        "tax_id": "12345678901",
        "collection_key": "pos-tx-persist",
    }
    body = json.dumps(payload).encode()

    class NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    with patch("module3_whatsapp_communicator.threading.Thread", NoopThread):
        resp = c.post(
            "/webhook/payment",
            data=body,
            content_type="application/json",
            headers={"X-Webhook-Signature": _sign(body)},
        )

    from state_store import get_default_store
    job = get_default_store().get_job(m3.PAYMENT_JOB_KIND, "pos-tx-persist")
    assert resp.status_code == 202
    assert job["status"] == "queued"
    assert job["payload"].startswith(m3.PAYMENT_JOB_PAYLOAD_PREFIX)
    assert "Test Hasta" not in job["payload"]
    assert "905321234567" not in job["payload"]
    assert "12345678901" not in job["payload"]
    assert m3._decode_payment_job_payload(job["payload"])["patient_name"] == "Test Hasta"


def test_payment_webhook_fails_when_pii_key_missing(client, monkeypatch):
    c, m3 = client
    monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
    import pii_crypto
    pii_crypto.reset_cache()

    payload = {
        "event": "payment.succeeded",
        "amount": "1500.00",
        "patient_name": "Test Hasta",
        "guardian_phone": "905321234567",
        "tax_id": "12345678901",
        "collection_key": "pos-tx-no-key",
    }
    body = json.dumps(payload).encode()

    resp = c.post(
        "/webhook/payment",
        data=body,
        content_type="application/json",
        headers={"X-Webhook-Signature": _sign(body)},
    )

    from state_store import get_default_store
    assert resp.status_code == 503
    assert resp.get_json()["status"] == "configuration_error"
    assert get_default_store().get_job(m3.PAYMENT_JOB_KIND, "pos-tx-no-key") is None


def test_payment_webhook_duplicate_does_not_reprocess(client):
    c, _ = client
    payload = {
        "event": "payment.succeeded",
        "amount": "1500.00",
        "patient_name": "Test Hasta",
        "guardian_phone": "905321234567",
        "tax_id": "12345678901",
        "collection_key": "pos-tx-dup",
    }
    body = json.dumps(payload).encode()

    with patch("main.trigger_esmm", return_value={"status": "done"}) as trigger_mock:
        first = c.post(
            "/webhook/payment",
            data=body,
            content_type="application/json",
            headers={"X-Webhook-Signature": _sign(body)},
        )
        import time
        for _ in range(20):
            if trigger_mock.called:
                break
            time.sleep(0.05)

    with patch("main.trigger_esmm") as second_trigger:
        second = c.post(
            "/webhook/payment",
            data=body,
            content_type="application/json",
            headers={"X-Webhook-Signature": _sign(body)},
        )

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.get_json()["status"] == "already_done"
    second_trigger.assert_not_called()


def test_process_payment_job_accepts_legacy_plaintext_payload(client):
    _, m3 = client
    payload = {
        "amount": "1500.00",
        "patient_name": "Legacy Hasta",
        "guardian_phone": "905321234567",
        "tax_id": "12345678901",
        "description": "Legacy job",
        "appointment_date": "",
        "collection_key": "legacy-job",
    }

    from state_store import get_default_store
    store = get_default_store()
    assert store.enqueue_job(
        m3.PAYMENT_JOB_KIND,
        "legacy-job",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )

    with patch("main.trigger_esmm", return_value={"status": "done"}) as trigger_mock:
        result = m3.process_payment_job("legacy-job")

    assert result["status"] == "done"
    trigger_mock.assert_called_once()
    assert trigger_mock.call_args.kwargs["patient_name"] == "Legacy Hasta"


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
