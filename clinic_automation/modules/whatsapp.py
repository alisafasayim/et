"""
WhatsApp Otomasyon Modülü
=========================
Twilio ve Evolution API desteği ile randevu hatırlatma,
form gönderimi ve hasta iletişimi.
"""

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
from abc import ABC, abstractmethod

from clinic_automation.config.settings import WhatsAppConfig

logger = logging.getLogger(__name__)


@dataclass
class WhatsAppMessage:
    """Gönderilen/alınan mesaj."""
    to: str
    body: str
    sent_at: Optional[datetime] = None
    status: str = "pending"  # pending, sent, delivered, read, failed
    message_sid: str = ""


class WhatsAppProvider(ABC):
    """WhatsApp sağlayıcısı soyut sınıfı."""

    @abstractmethod
    def send_message(self, to: str, body: str) -> WhatsAppMessage:
        pass

    @abstractmethod
    def send_template_message(self, to: str, template_name: str, params: dict) -> WhatsAppMessage:
        pass


class TwilioProvider(WhatsAppProvider):
    """Twilio WhatsApp Business API sağlayıcısı."""

    def __init__(self, config: WhatsAppConfig):
        self.config = config
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from twilio.rest import Client
            self._client = Client(
                self.config.twilio_account_sid,
                self.config.twilio_auth_token,
            )
        return self._client

    def send_message(self, to: str, body: str) -> WhatsAppMessage:
        """Serbest metin mesajı gönderir."""
        to_whatsapp = f"whatsapp:{to}" if not to.startswith("whatsapp:") else to
        try:
            message = self.client.messages.create(
                from_=self.config.twilio_whatsapp_number,
                body=body,
                to=to_whatsapp,
            )
            logger.info("Mesaj gönderildi: %s -> %s", to, message.sid)
            return WhatsAppMessage(
                to=to,
                body=body,
                sent_at=datetime.now(),
                status="sent",
                message_sid=message.sid,
            )
        except Exception as e:
            logger.error("Mesaj gönderilemedi: %s - %s", to, e)
            return WhatsAppMessage(to=to, body=body, status="failed")

    def send_template_message(self, to: str, template_name: str, params: dict) -> WhatsAppMessage:
        """Şablon mesajı gönderir (WhatsApp Business onaylı)."""
        # Twilio content templates kullanır
        to_whatsapp = f"whatsapp:{to}" if not to.startswith("whatsapp:") else to
        try:
            message = self.client.messages.create(
                from_=self.config.twilio_whatsapp_number,
                to=to_whatsapp,
                content_sid=template_name,
                content_variables=params,
            )
            return WhatsAppMessage(
                to=to, body=f"[Template: {template_name}]",
                sent_at=datetime.now(), status="sent", message_sid=message.sid,
            )
        except Exception as e:
            logger.error("Şablon mesajı gönderilemedi: %s", e)
            return WhatsAppMessage(to=to, body="", status="failed")


class EvolutionProvider(WhatsAppProvider):
    """Evolution API sağlayıcısı (self-hosted WhatsApp)."""

    def __init__(self, config: WhatsAppConfig):
        self.config = config

    def _make_request(self, endpoint: str, data: dict) -> dict:
        import requests
        url = f"{self.config.evolution_api_url}/{endpoint}"
        headers = {
            "apikey": self.config.evolution_api_key,
            "Content-Type": "application/json",
        }
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def send_message(self, to: str, body: str) -> WhatsAppMessage:
        """Metin mesajı gönderir."""
        # Numarayı temizle
        clean_number = to.replace("+", "").replace(" ", "").replace("-", "")
        try:
            result = self._make_request(
                f"message/sendText/{self.config.evolution_instance}",
                {"number": clean_number, "text": body},
            )
            return WhatsAppMessage(
                to=to, body=body, sent_at=datetime.now(),
                status="sent", message_sid=result.get("key", {}).get("id", ""),
            )
        except Exception as e:
            logger.error("Evolution mesaj hatası: %s", e)
            return WhatsAppMessage(to=to, body=body, status="failed")

    def send_template_message(self, to: str, template_name: str, params: dict) -> WhatsAppMessage:
        """Evolution API'de template desteği sınırlıdır; düz mesaj gönderir."""
        body = params.get("body", template_name)
        return self.send_message(to, body)


class WhatsAppAutomation:
    """WhatsApp otomasyon yöneticisi."""

    def __init__(self, config: WhatsAppConfig):
        self.config = config
        if config.provider == "twilio":
            self.provider = TwilioProvider(config)
        elif config.provider == "evolution":
            self.provider = EvolutionProvider(config)
        else:
            raise ValueError(f"Desteklenmeyen WhatsApp sağlayıcısı: {config.provider}")

    def send_appointment_reminder(
        self,
        patient_name: str,
        phone: str,
        appointment_time: datetime,
        doctor_name: str = "Dr.",
        form_url: str = "",
    ) -> WhatsAppMessage:
        """Randevu hatırlatma mesajı gönderir."""
        date_str = appointment_time.strftime("%d.%m.%Y")
        time_str = appointment_time.strftime("%H:%M")

        body = (
            f"Sayın {patient_name} Velisi,\n\n"
            f"{date_str} tarihinde saat {time_str}'de "
            f"{doctor_name} ile randevunuz bulunmaktadır.\n\n"
        )

        if form_url:
            body += (
                f"Randevu öncesi aşağıdaki formu doldurmanızı rica ederiz:\n"
                f"{form_url}\n\n"
            )

        body += (
            "Randevunuza gelemeyecekseniz lütfen en az 24 saat önceden "
            "bilgi veriniz.\n\nSağlıklı günler dileriz."
        )

        return self.provider.send_message(phone, body)

    def send_form_link(self, phone: str, patient_name: str, form_url: str) -> WhatsAppMessage:
        """Anamnez form linkini gönderir."""
        body = (
            f"Sayın {patient_name} Velisi,\n\n"
            f"İlk randevunuz öncesinde aşağıdaki formu doldurmanızı "
            f"rica ederiz. Bu bilgiler değerlendirme sürecini hızlandıracaktır.\n\n"
            f"Form: {form_url}\n\n"
            f"Teşekkür ederiz."
        )
        return self.provider.send_message(phone, body)

    def send_followup_reminder(
        self,
        phone: str,
        patient_name: str,
        next_date: datetime,
    ) -> WhatsAppMessage:
        """Kontrol randevusu hatırlatması gönderir."""
        date_str = next_date.strftime("%d.%m.%Y")
        body = (
            f"Sayın {patient_name} Velisi,\n\n"
            f"Kontrol randevunuz {date_str} tarihindedir. "
            f"Randevu saatinizi onaylamak için lütfen yanıt veriniz.\n\n"
            f"Sağlıklı günler dileriz."
        )
        return self.provider.send_message(phone, body)
