"""
Google Forms Entegrasyonu
=========================
Anamnez formlarını çeker ve hasta verileriyle eşleştirir.
"""

import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Optional

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from clinic_automation.config.settings import GoogleConfig

logger = logging.getLogger(__name__)


@dataclass
class FormResponse:
    """Tek bir form yanıtını temsil eder."""
    response_id: str
    submitted_at: datetime
    patient_name: str
    patient_age: str
    parent_name: str
    parent_phone: str
    complaint: str
    medical_history: str
    family_history: str
    school_info: str
    medications: str
    raw_answers: dict = field(default_factory=dict)


# Anamnez formundaki soru başlıklarının alan eşlemesi
DEFAULT_FIELD_MAPPING = {
    "Çocuğun Adı Soyadı": "patient_name",
    "Çocuğun Yaşı": "patient_age",
    "Veli Adı Soyadı": "parent_name",
    "Telefon Numarası": "parent_phone",
    "Başvuru Şikayeti": "complaint",
    "Tıbbi Geçmiş": "medical_history",
    "Aile Öyküsü": "family_history",
    "Okul Bilgisi": "school_info",
    "Kullandığı İlaçlar": "medications",
}


class GoogleFormsClient:
    """Google Forms API istemcisi."""

    def __init__(self, config: GoogleConfig, field_mapping: dict | None = None):
        self.config = config
        self.field_mapping = field_mapping or DEFAULT_FIELD_MAPPING
        self._service = None
        self._drive_service = None

    def authenticate(self) -> None:
        """OAuth2 ile kimlik doğrulama (Calendar ile aynı token)."""
        import os
        creds = None
        if os.path.exists(self.config.token_path):
            creds = Credentials.from_authorized_user_file(
                self.config.token_path, self.config.scopes
            )
        if not creds or not creds.valid:
            raise RuntimeError(
                "Google token bulunamadı. Önce Calendar modülü ile authenticate edin."
            )
        self._service = build("forms", "v1", credentials=creds)
        self._drive_service = build("drive", "v3", credentials=creds)
        logger.info("Google Forms kimlik doğrulaması başarılı.")

    @property
    def service(self):
        if self._service is None:
            self.authenticate()
        return self._service

    def get_form_structure(self, form_id: str | None = None) -> dict:
        """Form yapısını (soru başlıkları) getirir."""
        fid = form_id or self.config.form_id
        form = self.service.forms().get(formId=fid).execute()
        return {
            "title": form.get("info", {}).get("title", ""),
            "questions": [
                {
                    "id": item.get("questionItem", {}).get("question", {}).get("questionId", ""),
                    "title": item.get("title", ""),
                }
                for item in form.get("items", [])
                if "questionItem" in item
            ],
        }

    def get_responses(
        self,
        form_id: str | None = None,
        since: datetime | None = None,
    ) -> list[FormResponse]:
        """Form yanıtlarını getirir."""
        fid = form_id or self.config.form_id
        result = self.service.forms().responses().list(formId=fid).execute()
        responses = result.get("responses", [])

        # Soru ID -> başlık eşlemesi
        structure = self.get_form_structure(fid)
        question_map = {q["id"]: q["title"] for q in structure["questions"]}

        parsed = []
        for resp in responses:
            submitted = datetime.fromisoformat(
                resp.get("lastSubmittedTime", "").replace("Z", "+00:00")
            )
            if since and submitted < since:
                continue

            raw_answers = {}
            for q_id, answer_data in resp.get("answers", {}).items():
                title = question_map.get(q_id, q_id)
                text_answers = answer_data.get("textAnswers", {}).get("answers", [])
                value = text_answers[0].get("value", "") if text_answers else ""
                raw_answers[title] = value

            # Alan eşlemesi
            mapped = {}
            for form_title, field_name in self.field_mapping.items():
                for answer_title, answer_value in raw_answers.items():
                    if form_title.lower() in answer_title.lower():
                        mapped[field_name] = answer_value
                        break

            parsed.append(FormResponse(
                response_id=resp.get("responseId", ""),
                submitted_at=submitted,
                patient_name=mapped.get("patient_name", ""),
                patient_age=mapped.get("patient_age", ""),
                parent_name=mapped.get("parent_name", ""),
                parent_phone=mapped.get("parent_phone", ""),
                complaint=mapped.get("complaint", ""),
                medical_history=mapped.get("medical_history", ""),
                family_history=mapped.get("family_history", ""),
                school_info=mapped.get("school_info", ""),
                medications=mapped.get("medications", ""),
                raw_answers=raw_answers,
            ))

        logger.info("%d form yanıtı alındı.", len(parsed))
        return parsed

    def find_response_for_patient(
        self,
        patient_name: str,
        form_id: str | None = None,
    ) -> Optional[FormResponse]:
        """Belirli bir hasta için form yanıtını bulur."""
        from clinic_automation.utils.helpers import fuzzy_name_match

        responses = self.get_responses(form_id)
        for resp in responses:
            if fuzzy_name_match(patient_name, resp.patient_name):
                return resp
        return None
