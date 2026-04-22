"""
Google Forms Entegrasyonu
=========================
Anamnez formlarını çeker ve hasta verileriyle eşleştirir.

3 form tipi desteklenir:
- Okul Öncesi Ön Bilgi Formu
- Okul Sonrası (7-18) Ön Bilgi Formu
- Erişkin Ön Bilgi Formu
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
    # Genişletilmiş alanlar
    gender: str = ""
    tc_kimlik: str = ""
    birth_date: str = ""
    mother_name: str = ""
    father_name: str = ""
    mother_occupation: str = ""
    father_occupation: str = ""
    siblings: str = ""
    family_status: str = ""
    referral_source: str = ""
    birth_history: str = ""
    developmental_milestones: str = ""
    previous_psychiatry: str = ""
    main_concerns: str = ""
    strengths: str = ""
    form_type: str = ""  # okul_oncesi, okul_sonrasi, eriskin
    raw_answers: dict = field(default_factory=dict)

    def format_full_anamnesis(self) -> str:
        """Tüm form verilerini LLM'e göndermek için düz metin formatlar."""
        sections = []
        sections.append(f"FORM TİPİ: {self.form_type or 'Belirsiz'}")
        sections.append(f"DOLDURULMA TARİHİ: {self.submitted_at.strftime('%d.%m.%Y')}")
        sections.append("")

        if self.patient_name:
            sections.append(f"HASTA: {self.patient_name}")
        if self.patient_age or self.birth_date:
            sections.append(f"YAŞ/DOĞUM: {self.patient_age or ''} {self.birth_date or ''}")
        if self.gender:
            sections.append(f"CİNSİYET: {self.gender}")

        sections.append("")
        sections.append("--- AİLE BİLGİLERİ ---")
        if self.mother_name:
            sections.append(f"Anne: {self.mother_name} ({self.mother_occupation or '?'})")
        if self.father_name:
            sections.append(f"Baba: {self.father_name} ({self.father_occupation or '?'})")
        if self.family_status:
            sections.append(f"Aile durumu: {self.family_status}")
        if self.siblings:
            sections.append(f"Kardeşler: {self.siblings}")

        if self.school_info:
            sections.append(f"\n--- OKUL ---\n{self.school_info}")

        if self.birth_history:
            sections.append(f"\n--- DOĞUM/HAMİLELİK ---\n{self.birth_history}")
        if self.developmental_milestones:
            sections.append(f"\n--- GELİŞİM BASAMAKLARI ---\n{self.developmental_milestones}")

        if self.medical_history:
            sections.append(f"\n--- TIBBİ GEÇMİŞ ---\n{self.medical_history}")
        if self.medications:
            sections.append(f"Kullandığı ilaçlar: {self.medications}")

        if self.previous_psychiatry:
            sections.append(f"\n--- ÖNCEKİ PSİKİYATRİ BAŞVURUSU ---\n{self.previous_psychiatry}")

        if self.complaint or self.main_concerns:
            sections.append(f"\n--- BAŞVURU NEDENİ ---")
            if self.complaint:
                sections.append(f"Başlıca konu: {self.complaint}")
            if self.main_concerns:
                sections.append(f"Kaygılandıran sorunlar: {self.main_concerns}")

        if self.family_history:
            sections.append(f"\n--- AİLE PSİKİYATRİ ÖYKÜSÜ ---\n{self.family_history}")

        if self.strengths:
            sections.append(f"\n--- OLUMLU ÖZELLİKLER ---\n{self.strengths}")

        if self.referral_source:
            sections.append(f"\nYönlendiren: {self.referral_source}")

        # Eşlenmemiş tüm yanıtları da ekle (hiçbir veri kaybolmasın)
        unmapped_keys = set(self.raw_answers.keys()) - _get_mapped_keys()
        if unmapped_keys:
            sections.append("\n--- DİĞER FORM YANITLARI ---")
            for key in sorted(unmapped_keys):
                val = self.raw_answers[key]
                if val and val.strip():
                    sections.append(f"{key}: {val}")

        return "\n".join(sections)


def _get_mapped_keys() -> set:
    """Eşlenmiş soru başlıklarının kümesi."""
    mapped = set()
    for mapping in [FIELD_MAPPING_OKUL_ONCESI, FIELD_MAPPING_OKUL_SONRASI, FIELD_MAPPING_ERISKIN]:
        mapped.update(mapping.keys())
    return mapped


# ─── Form Soru Eşlemeleri (gerçek form başlıklarına göre) ───

FIELD_MAPPING_OKUL_ONCESI = {
    "Adı Soyadı": "patient_name",
    "T.C. Kimlik": "tc_kimlik",
    "Cinsiyeti": "gender",
    "Doğum Tarihi": "birth_date",
    "Okul/yuva": "school_info",
    "Sınıfı": "school_info",
    "Formu Dolduranın Adı": "parent_name",
    "Tel No": "parent_phone",
    "Sizi kim yönlendirdi": "referral_source",
    "Annenin Adı": "mother_name",
    "Annenin  Mesleği": "mother_occupation",
    "Annenin Mesleği": "mother_occupation",
    "Babanın Adı": "father_name",
    "Babanın  Mesleği": "father_occupation",
    "Babanın Mesleği": "father_occupation",
    "Aile Durumu": "family_status",
    "Kardeşler": "siblings",
    "psikiyatri başvurusu olan": "family_history",
    "Doğum ve hamilelik": "birth_history",
    "Doğum Ağırlığı": "birth_history",
    "ilk kelime": "developmental_milestones",
    "cümle kur": "developmental_milestones",
    "aylıkken yürüdü": "developmental_milestones",
    "Tuvalet eğitimi": "developmental_milestones",
    "rahatsızlıkları": "medical_history",
    "kullandığı ilaç": "medications",
    "ameliyat": "medical_history",
    "kaza": "medical_history",
    "psikiyatriye başvurdunuz": "previous_psychiatry",
    "başlıca konu": "complaint",
    "kaygılandıran": "main_concerns",
    "olumlu özellik": "strengths",
    "ele alınmasını": "complaint",
    "şikayetlerle": "complaint",
    "Konulan teşhis": "previous_psychiatry",
    "Verilen tedavi": "previous_psychiatry",
}

FIELD_MAPPING_OKUL_SONRASI = {
    **FIELD_MAPPING_OKUL_ONCESI,
    "Okuma-yazma": "developmental_milestones",
    "çiş ya da kaka": "medical_history",
    "kas seğirme": "medical_history",
    "tekrarlayıcı": "medical_history",
    "kaygı/korku": "medical_history",
    "Bedensel yakınma": "medical_history",
    "Dürtü": "medical_history",
}

FIELD_MAPPING_ERISKIN = {
    "İsim Soyisim": "patient_name",
    "Adı Soyadı": "patient_name",
    "TC kimlik": "tc_kimlik",
    "Doğum Tarihi": "birth_date",
    "Medeni Hal": "family_status",
    "çocuklarınızın yaşları": "siblings",
    "Eğitim durumu": "school_info",
    "Mesleği": "mother_occupation",
    "Çalışma durumu": "school_info",
    "Acil durumda aranacak": "parent_phone",
    "yönlendiren": "referral_source",
    "ruhsal hastalığı": "family_history",
    "aldığı tanı": "family_history",
    "Başvuru nedeni": "complaint",
    "teşhis kondu": "previous_psychiatry",
    "tedavi-tedaviler": "previous_psychiatry",
    "uzman": "previous_psychiatry",
    "Kullandığınız ilaçlar": "medications",
    "kullandığınız ilaç": "medications",
    "tedavinin yararı": "previous_psychiatry",
    "zarar verme": "main_concerns",
    "Eklemek istediğiniz": "main_concerns",
}

DEFAULT_FIELD_MAPPING = FIELD_MAPPING_OKUL_ONCESI


class GoogleFormsClient:
    """Google Forms API istemcisi — çoklu form desteği."""

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

    def detect_form_type(self, form_id: str | None = None) -> tuple[str, dict]:
        """Form tipini başlığından otomatik algılar ve uygun eşlemeyi döner."""
        structure = self.get_form_structure(form_id)
        title = structure.get("title", "").lower()

        if "okul öncesi" in title or "okul oncesi" in title:
            return "okul_oncesi", FIELD_MAPPING_OKUL_ONCESI
        elif "erişkin" in title or "eriskin" in title or "yetişkin" in title:
            return "eriskin", FIELD_MAPPING_ERISKIN
        else:
            return "okul_sonrasi", FIELD_MAPPING_OKUL_SONRASI

    def get_responses(
        self,
        form_id: str | None = None,
        since: datetime | None = None,
        field_mapping: dict | None = None,
    ) -> list[FormResponse]:
        """Form yanıtlarını getirir."""
        fid = form_id or self.config.form_id
        result = self.service.forms().responses().list(formId=fid).execute()
        responses = result.get("responses", [])

        structure = self.get_form_structure(fid)
        question_map = {q["id"]: q["title"] for q in structure["questions"]}

        # Form tipini algıla
        form_type, auto_mapping = self.detect_form_type(fid)
        mapping = field_mapping or auto_mapping

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
                if text_answers:
                    values = [a.get("value", "") for a in text_answers]
                    value = ", ".join(v for v in values if v)
                else:
                    value = ""
                raw_answers[title] = value

            # Alan eşlemesi (esnek: soru başlığında anahtar kelime aranır)
            mapped: dict[str, list[str]] = {}
            for form_title, field_name in mapping.items():
                for answer_title, answer_value in raw_answers.items():
                    if form_title.lower() in answer_title.lower() and answer_value:
                        mapped.setdefault(field_name, []).append(answer_value)

            def get_field(name: str) -> str:
                vals = mapped.get(name, [])
                return "; ".join(vals) if vals else ""

            parsed.append(FormResponse(
                response_id=resp.get("responseId", ""),
                submitted_at=submitted,
                patient_name=get_field("patient_name"),
                patient_age=get_field("patient_age"),
                parent_name=get_field("parent_name"),
                parent_phone=get_field("parent_phone"),
                complaint=get_field("complaint"),
                medical_history=get_field("medical_history"),
                family_history=get_field("family_history"),
                school_info=get_field("school_info"),
                medications=get_field("medications"),
                gender=get_field("gender"),
                tc_kimlik=get_field("tc_kimlik"),
                birth_date=get_field("birth_date"),
                mother_name=get_field("mother_name"),
                father_name=get_field("father_name"),
                mother_occupation=get_field("mother_occupation"),
                father_occupation=get_field("father_occupation"),
                siblings=get_field("siblings"),
                family_status=get_field("family_status"),
                referral_source=get_field("referral_source"),
                birth_history=get_field("birth_history"),
                developmental_milestones=get_field("developmental_milestones"),
                previous_psychiatry=get_field("previous_psychiatry"),
                main_concerns=get_field("main_concerns"),
                strengths=get_field("strengths"),
                form_type=form_type,
                raw_answers=raw_answers,
            ))

        logger.info("%d form yanıtı alındı (tip: %s).", len(parsed), form_type)
        return parsed

    def get_all_form_responses(
        self,
        form_ids: list[str],
        since: datetime | None = None,
    ) -> list[FormResponse]:
        """Birden fazla formdan tüm yanıtları toplar."""
        all_responses = []
        for fid in form_ids:
            try:
                responses = self.get_responses(fid, since)
                all_responses.extend(responses)
            except Exception as e:
                logger.warning("Form yanıtları alınamadı (%s): %s", fid, e)
        return all_responses

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

    def find_response_across_forms(
        self,
        patient_name: str,
        form_ids: list[str],
    ) -> Optional[FormResponse]:
        """Birden fazla formda hasta arar."""
        from clinic_automation.utils.helpers import fuzzy_name_match

        for fid in form_ids:
            try:
                responses = self.get_responses(fid)
                for resp in responses:
                    if fuzzy_name_match(patient_name, resp.patient_name):
                        return resp
            except Exception as e:
                logger.warning("Form aranırken hata (%s): %s", fid, e)
        return None
