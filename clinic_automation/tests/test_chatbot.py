"""
WhatsApp chatbot modülü için birim testleri.
"""

import pytest
from unittest.mock import MagicMock
from clinic_automation.modules.chatbot import (
    ChatbotEngine,
    Intent,
    IntentResult,
)
from clinic_automation.config.settings import WhatsAppConfig


@pytest.fixture
def config():
    cfg = WhatsAppConfig()
    cfg.chatbot_enabled = True
    cfg.chatbot_confidence_threshold = 0.70
    cfg.messaging_hours_start = 0   # Test için 24 saat açık
    cfg.messaging_hours_end = 23
    return cfg


@pytest.fixture
def bot(config):
    return ChatbotEngine(config)


class TestIntentClassification:
    def test_greeting(self, bot):
        result = bot.classify_intent("merhaba")
        assert result.intent == Intent.GREETING

    def test_appointment_query(self, bot):
        result = bot.classify_intent("randevum ne zaman?")
        assert result.intent == Intent.APPOINTMENT_QUERY

    def test_appointment_cancel(self, bot):
        result = bot.classify_intent("randevuyu iptal etmek istiyorum")
        assert result.intent == Intent.APPOINTMENT_CANCEL

    def test_appointment_confirm(self, bot):
        result = bot.classify_intent("evet geleceğiz")
        assert result.intent == Intent.APPOINTMENT_CONFIRM

    def test_medication_question(self, bot):
        result = bot.classify_intent("ilacın yan etkisi nedir?")
        assert result.intent == Intent.MEDICATION_QUESTION

    def test_thanks(self, bot):
        result = bot.classify_intent("teşekkür ederim")
        assert result.intent == Intent.THANKS

    def test_form_question(self, bot):
        result = bot.classify_intent("formu nasıl dolduracağım?")
        assert result.intent == Intent.FORM_QUESTION

    def test_doctor_contact(self, bot):
        result = bot.classify_intent("doktorla görüşmek istiyorum")
        assert result.intent == Intent.DOCTOR_CONTACT

    def test_opt_out(self, bot):
        result = bot.classify_intent("mesaj atmayın lütfen")
        assert result.intent == Intent.OPT_OUT

    def test_emergency_highest_priority(self, bot):
        # Acil durum normal kelimelerle karışık olsa bile öncelikli
        result = bot.classify_intent("randevum var ama intihar düşüncelerim de var")
        assert result.intent == Intent.EMERGENCY
        assert result.confidence == 1.0

    def test_emergency_keywords(self, bot):
        for keyword in ["intihar", "ölmek istiyorum", "kendime zarar"]:
            result = bot.classify_intent(keyword)
            assert result.intent == Intent.EMERGENCY, f"'{keyword}' acil olarak tanınmalı"

    def test_unknown_message(self, bot):
        result = bot.classify_intent("xyz123 anlamsız kelimeler abcdef")
        assert result.intent == Intent.UNKNOWN


class TestMessageProcessing:
    def test_emergency_response_contains_112(self, bot):
        response = bot.process_message("+905551234567", "intihar etmek istiyorum")
        assert "112" in response

    def test_greeting_response(self, bot):
        response = bot.process_message("+905551234567", "merhaba")
        assert len(response) > 0

    def test_appointment_cancel_response(self, bot):
        response = bot.process_message("+905551234567", "randevuyu iptal etmek istiyorum")
        assert "iptal" in response.lower() or "İptal" in response

    def test_thanks_response(self, bot):
        response = bot.process_message("+905551234567", "teşekkür ederim")
        assert len(response) > 0

    def test_opt_out_response(self, bot):
        response = bot.process_message("+905551234567", "mesaj atmayın")
        assert "iptal" in response.lower() or "abonelik" in response.lower()

    def test_conversation_state_created(self, bot):
        phone = "+905559999999"
        bot.process_message(phone, "merhaba")
        assert phone in bot.conversations

    def test_start_flow(self, bot):
        phone = "+905558888888"
        context = {
            "patient_name": "Ali Veli",
            "form_url": "https://forms.google.com/test",
        }
        response = bot.start_flow(phone, "form_reminder", context)
        assert "Ali Veli" in response or "form" in response.lower()


class TestIntentResult:
    def test_high_confidence_known_intent(self, bot):
        result = bot.classify_intent("intihar etmek istiyorum")
        assert result.confidence > 0.8

    def test_confidence_range(self, bot):
        for msg in ["merhaba", "randevum var mı", "teşekkürler"]:
            result = bot.classify_intent(msg)
            assert 0.0 <= result.confidence <= 1.0
