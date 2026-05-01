"""
Klinik Not Üretim Modülü
========================
LLM kullanarak transkriptten yapılandırılmış klinik
değerlendirme notu oluşturur.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from clinic_automation.config.settings import LLMConfig
from clinic_automation.modules.smart_matcher import PatientSegment
from clinic_automation.modules.google_forms import FormResponse

logger = logging.getLogger(__name__)


@dataclass
class ActionItem:
    """Görüşmede çıkan aksiyon kalemi."""
    action_type: str        # rapor_talebi, ilac_degisikligi, tetkik, yonlendirme, takip, aile_gorusmesi, okul_gorusmesi
    description: str
    priority: str = "normal"  # acil, normal, planli
    responsible: str = ""     # doktor, aile, okul, sosyal_hizmet
    deadline: str = ""


@dataclass
class ClinicalNote:
    """Yapılandırılmış klinik değerlendirme notu."""
    patient_name: str
    session_date: str
    chief_complaint: str        # Başvuru Şikayeti
    history_of_present: str     # Öykü (Anamnez)
    mental_status_exam: str     # Ruhsal Durum Muayenesi (RDM)
    developmental_history: str  # Gelişim Öyküsü
    family_history: str         # Aile Öyküsü
    diagnosis: str              # Tanı / Ön Tanı
    treatment_plan: str         # Tedavi Planı
    medications: str            # İlaç Tedavisi
    follow_up: str              # Kontrol / Takip Planı
    risk_assessment: str        # Risk Değerlendirmesi
    additional_notes: str       # Ek Notlar
    # Yeni alanlar: aksiyon kalemleri
    action_items: list[ActionItem] = field(default_factory=list)
    current_medications: list[dict] = field(default_factory=list)  # {name, dose, frequency, notes}
    next_appointment: str = ""
    family_report_requested: bool = False
    family_report_details: str = ""
    referrals: list[str] = field(default_factory=list)  # Yönlendirmeler
    raw_transcript: str = ""
    confidence_score: float = 0.0
    metadata: dict = field(default_factory=dict)


CLINICAL_NOTE_SYSTEM_PROMPT = """Sen deneyimli bir Çocuk ve Ergen Psikiyatristi uzmanının klinik asistanısın.
Sana verilen seans transkriptinden yapılandırılmış bir klinik değerlendirme notu oluşturman gerekiyor.

KURALLAR:
1. Sadece transkriptte geçen bilgileri kullan, ekleme yapma.
2. Tıbbi terminolojiyi doğru kullan ama anlaşılır ol.
3. Bilgi yoksa o alanı "Bu seansta değerlendirilmedi" olarak işaretle.
4. DSM-5-TR tanı kriterlerine uygun değerlendirme yap.
5. Çocuk-ergen psikiyatrisine özgü gelişimsel perspektifi koru.
6. Hasta mahremiyetine dikkat et, gereksiz detaydan kaçın.
7. İlaç dozları ve isimlerini transkriptte geçtiği şekilde yaz.
8. Risk faktörlerini (özkıyım, öz-zarar, istismar) titizlikle değerlendir.
9. MUTLAKA görüşmeden çıkan aksiyonları (rapor talebi, ilaç değişikliği, tetkik, yönlendirme) tespit et.
10. Aile "rapor", "sağlık kurulu", "durum bildiri", "engelli raporu" gibi ifadeler kullandıysa family_report_requested=true yap.
11. Bir sonraki kontrol tarihi (ör: "2 hafta sonra", "1 ay sonra") geçtiyse next_appointment alanına yaz.
12. Mevcut ilaçları ve doz bilgilerini current_medications listesine çıkar.
13. Geçmiş hasta dosyası bilgisi verilmişse, bu seanstaki değişimleri karşılaştırmalı değerlendir.

ÇIKTI FORMATI (JSON):
{
    "chief_complaint": "Başvuru Şikayeti",
    "history_of_present": "Öykü (bu seansta konuşulanlar)",
    "mental_status_exam": "Görünüm, davranış, duygudurum, düşünce içeriği, bilişsel durum",
    "developmental_history": "Gelişim öyküsüne dair bilgiler",
    "family_history": "Aile öyküsü",
    "diagnosis": "DSM-5-TR tanı/ön tanı",
    "treatment_plan": "Tedavi planı",
    "medications": "İlaç düzenlemesi özeti",
    "follow_up": "Kontrol planı",
    "risk_assessment": "Risk değerlendirmesi",
    "additional_notes": "Ek gözlemler",
    "action_items": [
        {"action_type": "rapor_talebi|ilac_degisikligi|tetkik|yonlendirme|takip|aile_gorusmesi|okul_gorusmesi",
         "description": "Yapılması gereken",
         "priority": "acil|normal|planli",
         "responsible": "doktor|aile|okul|sosyal_hizmet",
         "deadline": "varsa tarih veya süre"}
    ],
    "current_medications": [
        {"name": "İlaç adı", "dose": "Doz", "frequency": "Kullanım sıklığı", "notes": "Varsa not"}
    ],
    "next_appointment": "Önerilen kontrol tarihi/süresi",
    "family_report_requested": false,
    "family_report_details": "Rapor talebi detayları (varsa)",
    "referrals": ["Yönlendirme 1"]
}"""


CLINICAL_NOTE_USER_PROMPT = """Aşağıdaki seans transkriptinden klinik değerlendirme notu oluştur.

HASTA: {patient_name}
SEANS TARİHİ: {session_date}
SEANS SÜRESİ: {session_duration}

{patient_context_section}

{anamnesis_section}

TRANSKRİPT:
---
{transcript}
---

ÖNEMLİ: Transkriptte geçen TÜM aksiyonları (rapor talebi, ilaç düzenlemesi, tetkik istemi, yönlendirme, kontrol tarihi) action_items listesine ekle. Hiçbirini atlama.

Lütfen yukarıdaki JSON formatında yanıt ver."""


class ClinicalNoteGenerator:
    """LLM ile klinik not üreteci."""

    def __init__(self, config: LLMConfig):
        self.config = config

    def generate(
        self,
        patient_segment: PatientSegment,
        session_date: str,
        form_response: Optional[FormResponse] = None,
        patient_context: str = "",
    ) -> ClinicalNote:
        """Transkriptten klinik not üretir.

        Args:
            patient_segment: Transkript segmenti
            session_date: Seans tarihi
            form_response: Google Forms'tan gelen ön bilgi (anamnez) formu
            patient_context: Notion'daki mevcut hasta dosyası özeti
        """
        anamnesis_section = ""
        if form_response:
            anamnesis_section = self._format_anamnesis(form_response)

        patient_context_section = ""
        if patient_context:
            patient_context_section = (
                "MEVCUT HASTA DOSYASI:\n" + patient_context + "\n"
            )

        session_duration_min = (patient_segment.end_time - patient_segment.start_time) / 60

        user_prompt = CLINICAL_NOTE_USER_PROMPT.format(
            patient_name=patient_segment.patient_name,
            session_date=session_date,
            session_duration=f"{session_duration_min:.0f} dakika",
            patient_context_section=patient_context_section,
            anamnesis_section=anamnesis_section,
            transcript=patient_segment.transcript_text,
        )

        logger.info(
            "Klinik not üretiliyor: %s (%s)",
            patient_segment.patient_name, session_date,
        )

        if self.config.provider == "anthropic":
            raw_response = self._call_anthropic(user_prompt)
        else:
            raw_response = self._call_openai(user_prompt)

        return self._parse_response(
            raw_response,
            patient_segment.patient_name,
            session_date,
            patient_segment.transcript_text,
        )

    def _call_anthropic(self, user_prompt: str) -> str:
        """Anthropic Claude API çağrısı."""
        import anthropic

        client = anthropic.Anthropic(api_key=self.config.anthropic_api_key)
        message = client.messages.create(
            model=self.config.anthropic_model,
            max_tokens=4096,
            system=CLINICAL_NOTE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return message.content[0].text

    def _call_openai(self, user_prompt: str) -> str:
        """OpenAI API çağrısı."""
        from openai import OpenAI

        client = OpenAI(api_key=self.config.openai_api_key)
        response = client.chat.completions.create(
            model=self.config.openai_model,
            messages=[
                {"role": "system", "content": CLINICAL_NOTE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=4096,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    def _parse_response(
        self,
        raw: str,
        patient_name: str,
        session_date: str,
        transcript: str,
    ) -> ClinicalNote:
        """LLM yanıtını ClinicalNote objesine dönüştürür."""
        # JSON bloğunu çıkar
        try:
            # ```json ... ``` bloğu varsa çıkar
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0]

            data = json.loads(raw.strip())
        except (json.JSONDecodeError, IndexError) as e:
            logger.error("LLM yanıtı parse edilemedi: %s", e)
            data = {}

        # Aksiyon kalemlerini parse et
        action_items = []
        for item in data.get("action_items", []):
            if isinstance(item, dict):
                action_items.append(ActionItem(
                    action_type=item.get("action_type", "takip"),
                    description=item.get("description", ""),
                    priority=item.get("priority", "normal"),
                    responsible=item.get("responsible", ""),
                    deadline=item.get("deadline", ""),
                ))

        return ClinicalNote(
            patient_name=patient_name,
            session_date=session_date,
            chief_complaint=data.get("chief_complaint", "Belirtilmedi"),
            history_of_present=data.get("history_of_present", "Belirtilmedi"),
            mental_status_exam=data.get("mental_status_exam", "Belirtilmedi"),
            developmental_history=data.get("developmental_history", "Bu seansta değerlendirilmedi"),
            family_history=data.get("family_history", "Bu seansta değerlendirilmedi"),
            diagnosis=data.get("diagnosis", "Değerlendirme devam ediyor"),
            treatment_plan=data.get("treatment_plan", "Belirtilmedi"),
            medications=data.get("medications", "Bu seansta düzenleme yapılmadı"),
            follow_up=data.get("follow_up", "Belirtilmedi"),
            risk_assessment=data.get("risk_assessment", "Akut risk saptanmadı"),
            additional_notes=data.get("additional_notes", ""),
            action_items=action_items,
            current_medications=data.get("current_medications", []),
            next_appointment=data.get("next_appointment", ""),
            family_report_requested=data.get("family_report_requested", False),
            family_report_details=data.get("family_report_details", ""),
            referrals=data.get("referrals", []),
            raw_transcript=transcript,
            confidence_score=0.9 if data else 0.0,
        )

    @staticmethod
    def _format_anamnesis(form_response: FormResponse) -> str:
        """Form yanıtını anamnez formatına çevirir (tüm veriler dahil)."""
        return (
            "ÖN BİLGİLER (Anamnez Formu):\n"
            + form_response.format_full_anamnesis()
            + "\n"
        )
