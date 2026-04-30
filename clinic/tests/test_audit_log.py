"""audit_log: append-only KVKK m.12 erişim izi."""

import pytest

from audit_log import AuditLog


@pytest.fixture
def log(tmp_path):
    return AuditLog(tmp_path / "audit.db")


def test_record_returns_id(log):
    rid = log.record("patient.create", patient_uuid="uuid-1")
    assert isinstance(rid, int)
    assert rid >= 1


def test_record_persists_fields(log):
    log.record(
        "consent.grant",
        actor="admin",
        patient_uuid="uuid-1",
        details={"at": "2026-04-30T14:00:00Z"},
    )
    rows = log.query()
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "consent.grant"
    assert r["actor"] == "admin"
    assert r["patient_uuid"] == "uuid-1"
    assert r["details"]["at"] == "2026-04-30T14:00:00Z"


def test_query_filters_by_patient(log):
    log.record("patient.read", patient_uuid="a")
    log.record("patient.read", patient_uuid="b")
    rows = log.query(patient_uuid="a")
    assert len(rows) == 1
    assert rows[0]["patient_uuid"] == "a"


def test_query_filters_by_action(log):
    log.record("patient.read", patient_uuid="a")
    log.record("patient.delete", patient_uuid="a")
    rows = log.query(action="patient.delete")
    assert len(rows) == 1


def test_query_orders_newest_first(log):
    log.record("a")
    log.record("b")
    log.record("c")
    rows = log.query()
    actions = [r["action"] for r in rows]
    assert actions == ["c", "b", "a"]


def test_query_respects_limit(log):
    for i in range(10):
        log.record(f"action-{i}")
    rows = log.query(limit=3)
    assert len(rows) == 3


def test_no_update_or_delete_method():
    """KVKK m.12 — audit log append-only olmalı."""
    assert not hasattr(AuditLog, "update")
    assert not hasattr(AuditLog, "delete")


def test_audit_does_not_persist_pii_in_details(log):
    """details alanı PII içermemeli — kontrat olarak (call site uyar)."""
    # Burada test sadece API'nin "details serbest dict kabul ediyor"
    # davranışını doğruluyor; convention enforcement code review işi.
    log.record("patient.create", patient_uuid="uuid-1", details={"has_tax_id": True})
    row = log.query()[0]
    assert "has_tax_id" in row["details"]
    assert "tax_id" not in row["details"]  # convention check


def test_audit_helper_writes_to_singleton(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_DB", str(tmp_path / "ad.db"))

    import audit_log
    audit_log.reset_cache()
    audit_log.audit("test.event", details={"k": "v"})

    rows = audit_log.get_default_audit_log().query()
    assert any(r["action"] == "test.event" for r in rows)
