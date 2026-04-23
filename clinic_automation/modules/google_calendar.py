"""
Google Calendar Entegrasyonu
============================
Randevu verilerini çeker, hasta-zaman eşleştirmesi için kullanılır.
"""

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional

from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from clinic_automation.config.settings import GoogleConfig

logger = logging.getLogger(__name__)


@dataclass
class Appointment:
    """Tek bir randevuyu temsil eder."""
    patient_name: str
    start_time: datetime
    end_time: datetime
    event_id: str
    description: str = ""
    phone: str = ""
    status: str = "confirmed"  # confirmed, tentative, cancelled

    @property
    def duration_minutes(self) -> int:
        return int((self.end_time - self.start_time).total_seconds() / 60)


class GoogleCalendarClient:
    """Google Calendar API istemcisi."""

    def __init__(self, config: GoogleConfig):
        self.config = config
        self._service = None

    def authenticate(self) -> None:
        """Service account veya OAuth2 ile kimlik doğrulama yapar."""
        import os
        creds = None

        # Service account varsa öncelikli kullan
        sa_path = self.config.service_account_path
        if sa_path and os.path.exists(sa_path):
            creds = service_account.Credentials.from_service_account_file(
                sa_path, scopes=self.config.scopes
            )
            self._service = build("calendar", "v3", credentials=creds)
            logger.info("Google Calendar kimlik doğrulaması başarılı (service account).")
            return

        # Fallback: OAuth2
        if os.path.exists(self.config.token_path):
            creds = Credentials.from_authorized_user_file(
                self.config.token_path, self.config.scopes
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.config.credentials_path, self.config.scopes
                )
                creds = flow.run_local_server(port=0)

            with open(self.config.token_path, "w") as token:
                token.write(creds.to_json())

        self._service = build("calendar", "v3", credentials=creds)
        logger.info("Google Calendar kimlik doğrulaması başarılı.")

    @property
    def service(self):
        if self._service is None:
            self.authenticate()
        return self._service

    def get_appointments(
        self,
        date: datetime,
        calendar_id: Optional[str] = None,
    ) -> list[Appointment]:
        """Belirli bir günün randevularını getirir."""
        cal_id = calendar_id or self.config.calendar_id

        # Günün başı ve sonu (UTC)
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        events_result = self.service.events().list(
            calendarId=cal_id,
            timeMin=start_of_day.isoformat() + "Z",
            timeMax=end_of_day.isoformat() + "Z",
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        events = events_result.get("items", [])
        appointments = []

        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            end = event["end"].get("dateTime", event["end"].get("date"))

            # Telefon numarasını açıklamadan çıkarmaya çalış
            description = event.get("description", "")
            phone = self._extract_phone(description)

            appointments.append(Appointment(
                patient_name=event.get("summary", "Bilinmeyen Hasta"),
                start_time=datetime.fromisoformat(start),
                end_time=datetime.fromisoformat(end),
                event_id=event.get("id", ""),
                description=description,
                phone=phone,
                status=event.get("status", "confirmed"),
            ))

        logger.info(
            "%s tarihinde %d randevu bulundu.",
            date.strftime("%Y-%m-%d"),
            len(appointments),
        )
        return appointments

    def get_appointments_range(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[Appointment]:
        """Tarih aralığındaki tüm randevuları getirir."""
        all_appointments = []
        current = start_date
        while current <= end_date:
            all_appointments.extend(self.get_appointments(current))
            current += timedelta(days=1)
        return all_appointments

    def find_appointment_at_time(
        self,
        date: datetime,
        time: datetime,
        tolerance_minutes: int = 15,
    ) -> Optional[Appointment]:
        """Belirli bir saatteki randevuyu bulur (toleranslı)."""
        appointments = self.get_appointments(date)
        for appt in appointments:
            time_diff = abs((appt.start_time - time).total_seconds()) / 60
            if time_diff <= tolerance_minutes:
                return appt
        return None

    @staticmethod
    def _extract_phone(text: str) -> str:
        """Metinden telefon numarası çıkarır."""
        import re
        phone_pattern = r"(?:\+90|0)?[\s-]?(\d{3})[\s-]?(\d{3})[\s-]?(\d{2})[\s-]?(\d{2})"
        match = re.search(phone_pattern, text)
        if match:
            return f"+90{match.group(1)}{match.group(2)}{match.group(3)}{match.group(4)}"
        return ""
