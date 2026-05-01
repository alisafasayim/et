"""
Pytest konfigürasyonu ve ortak fixture'lar.
"""

import pytest
from clinic_automation.config.settings import (
    AppConfig, GoogleConfig, NotionConfig,
    WhatsAppConfig, TranscriptionConfig, LLMConfig, SecurityConfig,
)


@pytest.fixture
def test_config():
    """Test konfigürasyonu (gerçek API çağrısı yapmaz)."""
    return AppConfig(
        google=GoogleConfig(credentials_path="test_creds.json", form_id="test_form"),
        notion=NotionConfig(api_key="test_key", patients_db_id="test_db"),
        whatsapp=WhatsAppConfig(
            provider="twilio",
            twilio_account_sid="test_sid",
            twilio_auth_token="test_token",
            chatbot_enabled=True,
            chatbot_confidence_threshold=0.70,
            messaging_hours_start=0,
            messaging_hours_end=23,
        ),
        transcription=TranscriptionConfig(
            provider="openai",
            openai_api_key="test_key",
            audio_dir="/tmp/test_audio",
        ),
        llm=LLMConfig(
            provider="anthropic",
            anthropic_api_key="test_key",
        ),
        security=SecurityConfig(
            encryption_key_path="/tmp/test_enc_key",
            rsa_key_path="/tmp/test_rsa_key.pem",
            audit_log_path="/tmp/test_audit.log",
        ),
    )
