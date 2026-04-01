"""
Notion Entegrasyon Modülü
=========================
Hasta veritabanı yönetimi, klinik not ekleme,
randevu ve form verisi senkronizasyonu.
"""

import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Any

from notion_client import Client as NotionSDK

from clinic_automation.config.settings import NotionConfig
from clinic_automation.modules.clinical_notes import ClinicalNote
from clinic_automation.modules.google_calendar import Appointment
from clinic_automation.modules.google_forms import FormResponse
from clinic_automation.utils.helpers import fuzzy_name_match

logger = logging.getLogger(__name__)


@dataclass
class NotionPatient:
    """Notion'daki hasta kaydı."""
    page_id: str
    name: str
    age: str = ""
    parent_name: str = ""
    phone: str = ""
    diagnosis: str = ""
    status: str = "Aktif"  # Aktif, Pasif, Takip
    created_at: str = ""


class NotionClient:
    """Notion API istemcisi."""

    def __init__(self, config: NotionConfig):
        self.config = config
        self._client = None

    @property
    def client(self) -> NotionSDK:
        if self._client is None:
            self._client = NotionSDK(
                auth=self.config.api_key,
                notion_version=self.config.api_version,
            )
        return self._client

    # ─────────────────── Hasta Yönetimi ───────────────────

    def get_all_patients(self) -> list[NotionPatient]:
        """Tüm hastaları getirir."""
        results = []
        has_more = True
        start_cursor = None

        while has_more:
            query_params = {"database_id": self.config.patients_db_id}
            if start_cursor:
                query_params["start_cursor"] = start_cursor

            response = self.client.databases.query(**query_params)

            for page in response.get("results", []):
                props = page.get("properties", {})
                results.append(NotionPatient(
                    page_id=page["id"],
                    name=self._get_title(props.get("İsim", props.get("Name", {}))),
                    age=self._get_rich_text(props.get("Yaş", {})),
                    parent_name=self._get_rich_text(props.get("Veli Adı", {})),
                    phone=self._get_rich_text(props.get("Telefon", {})),
                    diagnosis=self._get_rich_text(props.get("Tanı", {})),
                    status=self._get_select(props.get("Durum", {})),
                    created_at=page.get("created_time", ""),
                ))

            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

        logger.info("%d hasta kaydı alındı.", len(results))
        return results

    def find_patient(self, name: str) -> Optional[NotionPatient]:
        """İsme göre hasta arar."""
        patients = self.get_all_patients()
        for patient in patients:
            if fuzzy_name_match(name, patient.name):
                return patient
        return None

    def create_patient(
        self,
        name: str,
        age: str = "",
        parent_name: str = "",
        phone: str = "",
        form_data: Optional[FormResponse] = None,
    ) -> NotionPatient:
        """Yeni hasta kaydı oluşturur."""
        properties: dict[str, Any] = {
            "İsim": {"title": [{"text": {"content": name}}]},
            "Durum": {"select": {"name": "Aktif"}},
        }

        if age or (form_data and form_data.patient_age):
            properties["Yaş"] = {
                "rich_text": [{"text": {"content": age or form_data.patient_age}}]
            }
        if parent_name or (form_data and form_data.parent_name):
            properties["Veli Adı"] = {
                "rich_text": [{"text": {"content": parent_name or form_data.parent_name}}]
            }
        if phone or (form_data and form_data.parent_phone):
            properties["Telefon"] = {
                "rich_text": [{"text": {"content": phone or form_data.parent_phone}}]
            }

        page = self.client.pages.create(
            parent={"database_id": self.config.patients_db_id},
            properties=properties,
        )

        logger.info("Yeni hasta oluşturuldu: %s (%s)", name, page["id"])

        # Form verisi varsa detayları sayfaya ekle
        if form_data:
            self._add_anamnesis_to_page(page["id"], form_data)

        return NotionPatient(
            page_id=page["id"],
            name=name,
            age=age or (form_data.patient_age if form_data else ""),
            parent_name=parent_name or (form_data.parent_name if form_data else ""),
            phone=phone or (form_data.parent_phone if form_data else ""),
            status="Aktif",
        )

    def get_or_create_patient(
        self,
        name: str,
        form_data: Optional[FormResponse] = None,
    ) -> NotionPatient:
        """Hastayı bul veya oluştur."""
        patient = self.find_patient(name)
        if patient:
            return patient
        return self.create_patient(name, form_data=form_data)

    # ─────────────────── Klinik Not Ekleme ───────────────────

    def add_clinical_note(
        self,
        patient: NotionPatient,
        note: ClinicalNote,
    ) -> str:
        """Klinik notu hasta sayfasına alt sayfa olarak ekler."""
        # Seans sayfası oluştur
        page = self.client.pages.create(
            parent={"database_id": self.config.sessions_db_id},
            properties={
                "Başlık": {
                    "title": [{"text": {"content": f"Seans - {note.session_date} - {note.patient_name}"}}]
                },
                "Hasta": {
                    "relation": [{"id": patient.page_id}]
                },
                "Tarih": {
                    "date": {"start": note.session_date}
                },
                "Tanı": {
                    "rich_text": [{"text": {"content": note.diagnosis[:2000]}}]
                },
            },
        )

        # Sayfa içeriğini oluştur
        blocks = self._build_clinical_note_blocks(note)
        self.client.blocks.children.append(block_id=page["id"], children=blocks)

        logger.info(
            "Klinik not eklendi: %s - %s (%s)",
            note.patient_name, note.session_date, page["id"],
        )
        return page["id"]

    def _build_clinical_note_blocks(self, note: ClinicalNote) -> list[dict]:
        """Klinik notu Notion blokları olarak yapılandırır."""
        blocks = []

        sections = [
            ("Başvuru Şikayeti", note.chief_complaint),
            ("Öykü", note.history_of_present),
            ("Ruhsal Durum Muayenesi", note.mental_status_exam),
            ("Gelişim Öyküsü", note.developmental_history),
            ("Aile Öyküsü", note.family_history),
            ("Tanı / Ön Tanı", note.diagnosis),
            ("Tedavi Planı", note.treatment_plan),
            ("İlaç Tedavisi", note.medications),
            ("Kontrol / Takip Planı", note.follow_up),
            ("Risk Değerlendirmesi", note.risk_assessment),
        ]

        if note.additional_notes:
            sections.append(("Ek Notlar", note.additional_notes))

        for title, content in sections:
            # Başlık
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": title}}]
                },
            })
            # İçerik (Notion'da 2000 karakter sınırı var, böl)
            for chunk in self._chunk_text(content, 2000):
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": chunk}}]
                    },
                })

            # Bölümler arası ayırıcı
            blocks.append({"object": "block", "type": "divider", "divider": {}})

        return blocks

    def _add_anamnesis_to_page(self, page_id: str, form: FormResponse) -> None:
        """Anamnez form verilerini hasta sayfasına ekler."""
        blocks = [
            {
                "object": "block",
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [{"type": "text", "text": {"content": "Anamnez Bilgileri"}}]
                },
            },
            {
                "object": "block",
                "type": "callout",
                "callout": {
                    "rich_text": [{"type": "text", "text": {"content": f"Form doldurulma tarihi: {form.submitted_at.strftime('%d.%m.%Y %H:%M')}"}}],
                    "icon": {"emoji": "📋"},
                },
            },
        ]

        fields = [
            ("Başvuru Şikayeti", form.complaint),
            ("Tıbbi Geçmiş", form.medical_history),
            ("Aile Öyküsü", form.family_history),
            ("Okul Bilgisi", form.school_info),
            ("Kullandığı İlaçlar", form.medications),
        ]

        for label, value in fields:
            if value:
                blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{"type": "text", "text": {"content": label}}]
                    },
                })
                for chunk in self._chunk_text(value, 2000):
                    blocks.append({
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"type": "text", "text": {"content": chunk}}]
                        },
                    })

        self.client.blocks.children.append(block_id=page_id, children=blocks)

    # ─────────────────── Randevu Senkronizasyonu ───────────────────

    def sync_appointment(self, appointment: Appointment) -> None:
        """Calendar randevusunu Notion'a yazar."""
        patient = self.get_or_create_patient(appointment.patient_name)

        # Randevuyu sessions DB'ye ekle (eğer yoksa)
        date_str = appointment.start_time.strftime("%Y-%m-%d")
        existing = self._find_session(patient.page_id, date_str)
        if existing:
            logger.debug("Randevu zaten mevcut: %s - %s", patient.name, date_str)
            return

        self.client.pages.create(
            parent={"database_id": self.config.sessions_db_id},
            properties={
                "Başlık": {
                    "title": [{"text": {"content": f"Randevu - {date_str} - {patient.name}"}}]
                },
                "Hasta": {"relation": [{"id": patient.page_id}]},
                "Tarih": {
                    "date": {
                        "start": appointment.start_time.isoformat(),
                        "end": appointment.end_time.isoformat(),
                    }
                },
            },
        )
        logger.info("Randevu senkronize edildi: %s - %s", patient.name, date_str)

    def _find_session(self, patient_page_id: str, date_str: str) -> Optional[dict]:
        """Belirli tarihte hasta seansı var mı kontrol eder."""
        response = self.client.databases.query(
            database_id=self.config.sessions_db_id,
            filter={
                "and": [
                    {"property": "Hasta", "relation": {"contains": patient_page_id}},
                    {"property": "Tarih", "date": {"equals": date_str}},
                ]
            },
        )
        results = response.get("results", [])
        return results[0] if results else None

    # ─────────────────── Yardımcı Fonksiyonlar ───────────────────

    @staticmethod
    def _get_title(prop: dict) -> str:
        title = prop.get("title", [])
        return title[0].get("text", {}).get("content", "") if title else ""

    @staticmethod
    def _get_rich_text(prop: dict) -> str:
        texts = prop.get("rich_text", [])
        return texts[0].get("text", {}).get("content", "") if texts else ""

    @staticmethod
    def _get_select(prop: dict) -> str:
        select = prop.get("select")
        return select.get("name", "") if select else ""

    @staticmethod
    def _chunk_text(text: str, max_length: int = 2000) -> list[str]:
        """Metni Notion karakter sınırına göre böler."""
        if len(text) <= max_length:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_length:
                chunks.append(text)
                break
            # Kelime sınırında böl
            split_at = text.rfind(" ", 0, max_length)
            if split_at == -1:
                split_at = max_length
            chunks.append(text[:split_at])
            text = text[split_at:].strip()
        return chunks
