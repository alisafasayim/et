"""
Review bulguları için hedefli regresyon testleri.
"""

from datetime import datetime
from types import SimpleNamespace

from clinic_automation.config.settings import GoogleConfig, NotionConfig
from clinic_automation.modules.clinical_notes import ClinicalNote
from clinic_automation.modules.google_calendar import GoogleCalendarClient
from clinic_automation.modules.notion_client import NotionClient
from clinic_automation.scripts import webhook_server


class FakeEncryptionManager:
    def encrypt_dict_fields(self, data, field_names=None):
        return {key: f"ENC:{key}" for key in data}


def encrypted_notion_client() -> NotionClient:
    security = SimpleNamespace(
        field_level_encryption=True,
        encrypt_transcripts=True,
        encryption_key_path="",
        rsa_key_path="",
    )
    return NotionClient(
        NotionConfig(),
        security_config=security,
        encryption_manager=FakeEncryptionManager(),
    )


def test_twilio_response_escapes_xml():
    response = webhook_server._twilio_response("a < b & c > d")

    assert b"a &lt; b &amp; c &gt; d" in response.data
    assert b"a < b & c > d" not in response.data


def test_twilio_signature_fails_closed_without_token(monkeypatch):
    monkeypatch.setattr(webhook_server.config.whatsapp, "twilio_auth_token", "")

    with webhook_server.app.test_request_context("/webhook/twilio", method="POST"):
        assert webhook_server._verify_twilio_signature(webhook_server.request) is False


def test_clinical_note_blocks_encrypt_sensitive_content():
    client = encrypted_notion_client()
    note = ClinicalNote(
        patient_name="Test Hasta",
        session_date="2024-03-15",
        chief_complaint="Hassas basvuru sikayeti",
        history_of_present="Hassas oyku",
        mental_status_exam="Ruhsal durum",
        developmental_history="Gelisim",
        family_history="Aile oykusu",
        diagnosis="Gizli tani",
        treatment_plan="Tedavi plani",
        medications="Ilac bilgisi",
        follow_up="Kontrol",
        risk_assessment="Risk bilgisi",
        additional_notes="Ek not",
    )

    blocks = client._build_clinical_note_blocks(note)
    block_text = str(blocks)

    assert "ENC:" in block_text
    assert "Gizli tani" not in block_text
    assert "Ilac bilgisi" not in block_text
    assert "Risk bilgisi" not in block_text


class FakeEventsList:
    def __init__(self):
        self.kwargs = None

    def list(self, **kwargs):
        self.kwargs = kwargs
        return self

    def execute(self):
        return {"items": []}


class FakeCalendarService:
    def __init__(self):
        self.events_list = FakeEventsList()

    def events(self):
        return self.events_list


def test_calendar_query_uses_local_timezone_window():
    service = FakeCalendarService()
    client = GoogleCalendarClient(GoogleConfig())
    client._service = service

    client.get_appointments(datetime(2024, 3, 15))

    assert service.events_list.kwargs["timeZone"] == "Europe/Istanbul"
    assert service.events_list.kwargs["timeMin"].endswith("+03:00")
    assert not service.events_list.kwargs["timeMin"].endswith("Z")
