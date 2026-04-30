"""medications: SOAP parse + reconcile akışı."""

from unittest.mock import patch

import pytest

from medications import parse_medications_from_text


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def test_parse_simple_dose_and_drug():
    result = parse_medications_from_text("Risperdal 1 mg, akşam tek doz başlandı")
    assert len(result) == 1
    assert result[0]["drug_name"] == "Risperdal"
    assert result[0]["dose"] == "1mg"


def test_parse_multiple_drugs():
    text = "Concerta 36 mg sabah; Strattera 25 mg akşam başlandı."
    result = parse_medications_from_text(text)
    names = sorted(r["drug_name"].lower() for r in result)
    assert "concerta" in names
    assert "strattera" in names


def test_parse_empty_text():
    assert parse_medications_from_text("") == []
    assert parse_medications_from_text("Bilinmeyen şikayet") == []


def test_parse_drug_without_dose():
    """Doz belirtilmeden sadece ilaç adı geçerse de yakala."""
    result = parse_medications_from_text("Melatonin denenebilir.")
    assert len(result) == 1
    assert result[0]["drug_name"] == "Melatonin"
    assert result[0]["dose"] == ""


def test_parse_decimal_dose():
    result = parse_medications_from_text("Risperdal 0.5 mg başlandı")
    assert result[0]["dose"].replace(" ", "") in ("0.5mg", "0,5mg")


def test_parse_recognizes_generic_names():
    """Marka değil etken madde adıyla yazılırsa da yakala."""
    result = parse_medications_from_text("Aripiprazol 10 mg eklendi.")
    assert any(r["drug_name"].lower() == "aripiprazol" for r in result)


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

def _soap_with_meds(text: str, patient: str = "Ali Yıldız") -> dict:
    return {
        "patient_name": patient,
        "soap": {"plan": {"medication": text}},
    }


def test_reconcile_no_op_when_no_db_configured(monkeypatch):
    monkeypatch.setattr("medications.NOTION_MEDICATIONS_DATABASE_ID", "")
    from medications import reconcile_medications_from_soap
    result = reconcile_medications_from_soap(_soap_with_meds("Risperdal 1 mg"))
    assert result["skipped"] == "no_db"


def test_reconcile_no_op_for_unknown_patient(monkeypatch):
    monkeypatch.setattr("medications.NOTION_MEDICATIONS_DATABASE_ID", "db-test")
    from medications import reconcile_medications_from_soap
    result = reconcile_medications_from_soap(
        _soap_with_meds("Risperdal 1 mg", patient="unknown")
    )
    assert result["skipped"] == "no_patient"


def test_reconcile_adds_new_drug(monkeypatch):
    monkeypatch.setattr("medications.NOTION_MEDICATIONS_DATABASE_ID", "db-test")
    monkeypatch.setattr("medications.NOTION_TOKEN", "tok")

    with patch("medications.list_active_medications", return_value=[]), \
         patch("medications.add_medication", return_value="page-1") as add_mock, \
         patch("medications.mark_medication_status") as mark_mock:
        from medications import reconcile_medications_from_soap
        result = reconcile_medications_from_soap(
            _soap_with_meds("Risperdal 1 mg başlandı.")
        )

    assert len(result["added"]) == 1
    assert result["added"][0]["drug_name"] == "Risperdal"
    assert result["ended"] == []
    add_mock.assert_called_once()
    mark_mock.assert_not_called()


def test_reconcile_skips_already_active_drug(monkeypatch):
    """SOAP'ta yine bahsedilen, mevcut Aktif ilaç → yeniden eklenmez."""
    monkeypatch.setattr("medications.NOTION_MEDICATIONS_DATABASE_ID", "db-test")
    monkeypatch.setattr("medications.NOTION_TOKEN", "tok")

    existing = [{"page_id": "p-existing", "drug_name": "Risperdal", "dose": "1mg"}]
    with patch("medications.list_active_medications", return_value=existing), \
         patch("medications.add_medication") as add_mock, \
         patch("medications.mark_medication_status") as mark_mock:
        from medications import reconcile_medications_from_soap
        result = reconcile_medications_from_soap(
            _soap_with_meds("Risperdal 1 mg devam ediyor.")
        )

    assert result["added"] == []
    assert result["ended"] == []
    add_mock.assert_not_called()
    mark_mock.assert_not_called()


def test_reconcile_marks_ended_drug_when_no_longer_mentioned(monkeypatch):
    """Aktif ilaç SOAP'ta hiç geçmiyor → Sonlandırıldı."""
    monkeypatch.setattr("medications.NOTION_MEDICATIONS_DATABASE_ID", "db-test")
    monkeypatch.setattr("medications.NOTION_TOKEN", "tok")

    existing = [{"page_id": "p-old", "drug_name": "Concerta", "dose": "36mg"}]
    with patch("medications.list_active_medications", return_value=existing), \
         patch("medications.add_medication", return_value="p-new") as add_mock, \
         patch("medications.mark_medication_status") as mark_mock:
        from medications import reconcile_medications_from_soap
        result = reconcile_medications_from_soap(
            _soap_with_meds("Risperdal 1 mg eklendi.")
        )

    assert any(m["drug_name"] == "Risperdal" for m in result["added"])
    assert any(m["drug_name"] == "Concerta" for m in result["ended"])
    mark_mock.assert_called_once_with("p-old", "Sonlandırıldı")
