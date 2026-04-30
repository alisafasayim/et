"""KVKK hibrit mod: M2 Notion'a PII göndermez, pseudonym kullanır."""

from unittest.mock import patch

import pytest

cryptography = pytest.importorskip("cryptography", reason="cryptography yüklü değil")
pytest.importorskip("google.oauth2.credentials", reason="google-auth yüklü değil")

from cryptography.fernet import Fernet


@pytest.fixture
def m2_hybrid(tmp_path, monkeypatch):
    """KVKK hibrit aktif, hierarchical aktif, izole DB."""
    monkeypatch.setenv("NOTION_TOKEN", "test")
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-test")
    monkeypatch.setenv("NOTION_HIERARCHICAL_MODE", "true")
    monkeypatch.setenv("KVKK_HYBRID_MODE", "true")
    monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PII_HASH_KEY", "test-salt")
    monkeypatch.setenv("PATIENT_REGISTRY_DB", str(tmp_path / "reg.db"))

    import importlib
    import patient_registry, pii_crypto
    pii_crypto.reset_cache()
    patient_registry.reset_cache()
    import module2_notion_archiver as mod
    importlib.reload(mod)
    return mod


def _soap(name: str = "Ali Yıldız Demir") -> dict:
    return {
        "patient_name": name,
        "appointment_id": "evt-1",
        "appointment_start": "2026-04-30T14:00:00+03:00",
        "generated_at": "2026-04-30T14:30:00+00:00",
        "soap": {
            "subjective": {
                "chief_complaint": f"{name} okulda dikkat sorunu yaşıyor",
                "history_of_present_illness": "Telefon: 0532 123 45 67. TC: 12345678901",
            },
            "objective": {},
            "assessment": {"risk_assessment": "düşük"},
            "plan": {"medication": "Risperdal 1 mg"},
        },
    }


def test_first_session_creates_root_with_pseudonym(m2_hybrid):
    """İlk seans → registry'de UUID oluştur, Notion kök sayfası
    pseudonym ile yazılır, gerçek isim Notion API'sine GİTMEZ."""
    captured_create = []

    def fake_create_root(name: str) -> str:
        captured_create.append(name)
        return "root-1"

    with patch.object(m2_hybrid, "create_patient_root_page", side_effect=fake_create_root), \
         patch.object(m2_hybrid, "find_patient_root_page", return_value=None), \
         patch.object(m2_hybrid, "count_existing_session_subpages", return_value=0), \
         patch.object(m2_hybrid, "create_session_subpage", return_value="sess-1"), \
         patch.object(m2_hybrid, "_append_blocks"), \
         patch.object(m2_hybrid, "append_anamnesis_to_page"), \
         patch.object(m2_hybrid, "append_soap_to_page") as soap_mock:
        m2_hybrid.archive_patient_session(_soap(), form_id="", all_form_responses=None)

    # Notion'a yazılan ad pseudonym formatında
    assert len(captured_create) == 1
    assert captured_create[0].startswith("#"), f"Notion'a gerçek ad gitti: {captured_create[0]}"
    assert "Ali Yıldız Demir" not in captured_create[0]

    # SOAP'ta hasta adı yıkanmış mı?
    soap_arg = soap_mock.call_args.args[1]
    assert soap_arg["patient_name"].startswith("#")
    assert "Ali Yıldız Demir" not in soap_arg["soap"]["subjective"]["chief_complaint"]


def test_second_session_reuses_registry_and_notion_root(m2_hybrid):
    """İkinci seansta UUID ve Notion kök tekrar oluşturulmaz."""
    # İlk seans
    with patch.object(m2_hybrid, "create_patient_root_page", return_value="root-1"), \
         patch.object(m2_hybrid, "find_patient_root_page", return_value=None), \
         patch.object(m2_hybrid, "count_existing_session_subpages", return_value=0), \
         patch.object(m2_hybrid, "create_session_subpage", return_value="sess-1"), \
         patch.object(m2_hybrid, "_append_blocks"), \
         patch.object(m2_hybrid, "append_anamnesis_to_page"), \
         patch.object(m2_hybrid, "append_soap_to_page"):
        m2_hybrid.archive_patient_session(_soap(), form_id="", all_form_responses=None)

    # İkinci seans — registry'de UUID + notion_page_id var
    with patch.object(m2_hybrid, "create_patient_root_page") as create_mock, \
         patch.object(m2_hybrid, "find_patient_root_page") as find_mock, \
         patch.object(m2_hybrid, "count_existing_session_subpages", return_value=1), \
         patch.object(m2_hybrid, "create_session_subpage", return_value="sess-2"), \
         patch.object(m2_hybrid, "_append_blocks"), \
         patch.object(m2_hybrid, "append_anamnesis_to_page"), \
         patch.object(m2_hybrid, "append_soap_to_page"):
        m2_hybrid.archive_patient_session(_soap(), form_id="", all_form_responses=None)

    # Yeniden oluşturulmadı
    create_mock.assert_not_called()
    # Hatta find_patient_root_page bile çağrılmamalı (registry cached)
    find_mock.assert_not_called()


def test_pii_in_soap_is_redacted(m2_hybrid):
    """SOAP içindeki telefon/TCKN logging redact ile maskelenir."""
    captured_soap = {}

    def capture(page_id, soap):
        captured_soap.update(soap)

    with patch.object(m2_hybrid, "create_patient_root_page", return_value="root-1"), \
         patch.object(m2_hybrid, "find_patient_root_page", return_value=None), \
         patch.object(m2_hybrid, "count_existing_session_subpages", return_value=0), \
         patch.object(m2_hybrid, "create_session_subpage", return_value="sess-1"), \
         patch.object(m2_hybrid, "_append_blocks"), \
         patch.object(m2_hybrid, "append_anamnesis_to_page"), \
         patch.object(m2_hybrid, "append_soap_to_page", side_effect=capture):
        m2_hybrid.archive_patient_session(_soap(), form_id="", all_form_responses=None)

    history = captured_soap["soap"]["subjective"]["history_of_present_illness"]
    assert "0532 123 45 67" not in history
    assert "12345678901" not in history


def test_form_response_pii_scrubbed(m2_hybrid):
    """Anamnez form yanıtlarında PII redact edilir."""
    form = {
        "answers": {
            "Ad Soyad": "Ali Yıldız Demir",
            "Telefon": "0532 123 45 67",
            "Şikayet": "Yıldız çok sinirli",  # ≥4 karakter parça → yakalanır
            "Kısa İsim": "Ali iyi",  # 3 karakter "Ali" → false positive risk, yakalanmaz
        }
    }
    captured = {}

    def capture_form(page_id, response):
        if response is not None:
            captured["form"] = response

    with patch.object(m2_hybrid, "create_patient_root_page", return_value="root-1"), \
         patch.object(m2_hybrid, "find_patient_root_page", return_value=None), \
         patch.object(m2_hybrid, "count_existing_session_subpages", return_value=0), \
         patch.object(m2_hybrid, "create_session_subpage", return_value="sess-1"), \
         patch.object(m2_hybrid, "append_anamnesis_to_page", side_effect=capture_form), \
         patch.object(m2_hybrid, "append_soap_to_page"):
        m2_hybrid.archive_patient_session(
            _soap(), form_id="", all_form_responses=[form]
        )

    answers = captured["form"]["answers"]
    assert "0532 123 45 67" not in answers["Telefon"]
    # Hasta adı pseudonym ile değiştirilmeli
    assert answers["Ad Soyad"].startswith("#")
    # ≥4 karakter ad parçası ("Yıldız") yakalanır
    assert "Yıldız" not in answers["Şikayet"]
    # 3-karakter "Ali" yaygın bir kelime; false positive riski yüzünden
    # bilerek atlandı. Klinik kabul: kısa adlar manuel kontrol edilmeli.
    assert "Ali" in answers["Kısa İsim"]


def test_hybrid_off_unchanged_behavior(monkeypatch, tmp_path):
    """KVKK_HYBRID_MODE=false → registry kullanılmaz, gerçek ad yazılır."""
    monkeypatch.setenv("NOTION_TOKEN", "test")
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-test")
    monkeypatch.setenv("NOTION_HIERARCHICAL_MODE", "true")
    monkeypatch.setenv("KVKK_HYBRID_MODE", "false")

    import importlib
    import module2_notion_archiver as mod
    importlib.reload(mod)

    captured_create = []

    def fake_create(name: str) -> str:
        captured_create.append(name)
        return "root-1"

    with patch.object(mod, "create_patient_root_page", side_effect=fake_create), \
         patch.object(mod, "find_patient_root_page", return_value=None), \
         patch.object(mod, "count_existing_session_subpages", return_value=0), \
         patch.object(mod, "create_session_subpage", return_value="sess-1"), \
         patch.object(mod, "_append_blocks"), \
         patch.object(mod, "append_anamnesis_to_page"), \
         patch.object(mod, "append_soap_to_page"):
        mod.archive_patient_session(_soap(), form_id="", all_form_responses=None)

    # Hibrit kapalı → gerçek ad Notion'a gitti
    assert captured_create[0] == "Ali Yıldız Demir"
