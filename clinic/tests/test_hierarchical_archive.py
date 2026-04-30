"""M2 hasta-seans hiyerarşik arşivleme akışı."""

from unittest.mock import patch

import pytest

pytest.importorskip("google.oauth2.credentials", reason="google-auth yüklü değil")


@pytest.fixture
def m2(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "test")
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-test")
    monkeypatch.setenv("NOTION_HIERARCHICAL_MODE", "true")

    import importlib
    import module2_notion_archiver as mod
    importlib.reload(mod)
    return mod


def _soap(patient: str = "Ali Yıldız", appt_id: str = "evt-1") -> dict:
    return {
        "patient_name": patient,
        "appointment_id": appt_id,
        "appointment_start": "2026-04-30T14:00:00+03:00",
        "generated_at": "2026-04-30T14:30:00+00:00",
        "soap": {
            "subjective": {"chief_complaint": "test"},
            "objective": {},
            "assessment": {},
            "plan": {},
        },
    }


def test_first_session_creates_root_and_writes_anamnesis(m2):
    """İlk seans → kök sayfa oluşturulur + anamnez kök sayfaya yazılır."""
    with patch.object(m2, "find_patient_root_page", return_value=None) as find_mock, \
         patch.object(m2, "create_patient_root_page", return_value="root-1") as create_root, \
         patch.object(m2, "count_existing_session_subpages", return_value=0) as count_mock, \
         patch.object(m2, "append_anamnesis_to_page") as anamnesis_mock, \
         patch.object(m2, "create_session_subpage", return_value="sess-1") as create_session, \
         patch.object(m2, "append_soap_to_page") as soap_mock:
        result = m2.archive_patient_session(
            _soap(),
            form_id="",
            all_form_responses=[{"answers": {"Ad Soyad": "Ali Yıldız"}}],
        )

    assert result == "sess-1"
    find_mock.assert_called_once()
    create_root.assert_called_once()
    # Anamnez kök sayfaya bir kez basıldı
    anamnesis_mock.assert_called_once()
    args, _ = anamnesis_mock.call_args
    assert args[0] == "root-1"
    # SOAP seans sayfasına basıldı
    soap_mock.assert_called_once_with("sess-1", _soap())
    # Seans başlığı tarih ile başlıyor
    create_session.assert_called_once()
    title = create_session.call_args.args[1]
    assert "2026-04-30" in title
    assert "Seans 1" in title


def test_second_session_reuses_root_skips_anamnesis(m2):
    """İkinci seans → kök yeniden kullanılır, anamnez TEKRAR yazılmaz."""
    with patch.object(m2, "find_patient_root_page", return_value="root-1"), \
         patch.object(m2, "create_patient_root_page") as create_root_mock, \
         patch.object(m2, "count_existing_session_subpages", return_value=1), \
         patch.object(m2, "append_anamnesis_to_page") as anamnesis_mock, \
         patch.object(m2, "create_session_subpage", return_value="sess-2"), \
         patch.object(m2, "append_soap_to_page"):
        m2.archive_patient_session(
            _soap(),
            form_id="",
            all_form_responses=[{"answers": {"Ad Soyad": "Ali Yıldız"}}],
        )

    create_root_mock.assert_not_called()  # Mevcut kök yeniden kullanıldı
    anamnesis_mock.assert_not_called()    # İkinci+ seansta anamnez basılmaz


def test_session_numbering(m2):
    """count=0 → Seans 1, count=2 → Seans 3, count=-1 → numara yok."""
    cases = [(0, "Seans 1"), (2, "Seans 3"), (-1, None)]
    for count, expected_label in cases:
        with patch.object(m2, "find_patient_root_page", return_value="root-1"), \
             patch.object(m2, "count_existing_session_subpages", return_value=count), \
             patch.object(m2, "append_anamnesis_to_page"), \
             patch.object(m2, "create_session_subpage", return_value="sess-x") as create_mock, \
             patch.object(m2, "append_soap_to_page"):
            m2.archive_patient_session(
                _soap(), form_id="", all_form_responses=None
            )
        title = create_mock.call_args.args[1]
        if expected_label:
            assert expected_label in title, f"count={count}, başlık: {title}"
        else:
            assert "Seans" not in title


def test_flat_mode_unchanged_when_flag_disabled(monkeypatch):
    """NOTION_HIERARCHICAL_MODE=false → eski create_patient_page akışı."""
    monkeypatch.setenv("NOTION_TOKEN", "test")
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-test")
    monkeypatch.setenv("NOTION_HIERARCHICAL_MODE", "false")

    import importlib
    import module2_notion_archiver as mod
    importlib.reload(mod)

    with patch.object(mod, "create_patient_page", return_value="page-flat") as flat_mock, \
         patch.object(mod, "append_anamnesis_to_page"), \
         patch.object(mod, "append_soap_to_page"), \
         patch.object(mod, "find_patient_root_page") as hier_mock:
        result = mod.archive_patient_session(_soap(), form_id="", all_form_responses=None)

    flat_mock.assert_called_once()
    hier_mock.assert_not_called()  # Hierarchical fonksiyon hiç çağrılmadı
    assert result == "page-flat"
