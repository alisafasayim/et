"""
Hasta Yolculuğu İş Akışı
=========================
Başvuru -> Triyaj -> Değerlendirme -> Tanı -> Tedavi -> İzlem

Doküman: cocuk_ergen_psikiyatri_klinik_workflow.md
"""

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class JourneyStage(Enum):
    BASVURU = "basvuru"                # İlk başvuru/kayıt
    TRIYAJ = "triyaj"                  # Ön değerlendirme ve aciliyet
    ON_DEGERLENDIRME = "on_degerlendirme"  # Form doldurma, anamnez
    KLINIK_DEGERLENDIRME = "klinik_degerlendirme"  # İlk görüşme
    TANI = "tani"                      # Tanı ve formülasyon
    TEDAVI = "tedavi"                  # Aktif tedavi süreci
    IZLEM = "izlem"                    # Kontrol ve takip
    SONLANDIRMA = "sonlandirma"        # Tedavi sonlandırma
    PASIF = "pasif"                    # Takipten çıkmış


class Priority(Enum):
    ROUTINE = "rutin"           # Standart süre
    SOON = "yakin"              # 1 hafta içinde
    URGENT = "acil"             # 24-48 saat içinde
    EMERGENCY = "acil_kriz"     # Hemen


@dataclass
class JourneyEvent:
    """Hasta yolculuğundaki tek bir olay."""
    stage: JourneyStage
    timestamp: datetime
    action: str
    details: str = ""
    performed_by: str = "system"
    auto_triggered: bool = False


@dataclass
class PatientJourney:
    """Bir hastanın tam yolculuğu."""
    patient_id: str
    patient_name: str
    current_stage: JourneyStage
    priority: Priority
    events: list[JourneyEvent] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)


# Her aşama için otomatik aksiyonlar
STAGE_AUTOMATIONS: dict[JourneyStage, list[dict]] = {
    JourneyStage.BASVURU: [
        {"action": "create_patient_record", "description": "Hasta kaydı oluştur"},
        {"action": "send_welcome_message", "description": "Hoş geldiniz mesajı gönder"},
        {"action": "send_intake_form", "description": "Anamnez formu gönder"},
        {"action": "schedule_triage", "description": "Triyaj planla"},
    ],
    JourneyStage.TRIYAJ: [
        {"action": "assess_urgency", "description": "Aciliyet değerlendirmesi yap"},
        {"action": "check_form_completion", "description": "Form doldurulma durumunu kontrol et"},
        {"action": "assign_priority", "description": "Öncelik belirle"},
    ],
    JourneyStage.ON_DEGERLENDIRME: [
        {"action": "remind_form", "description": "Form hatırlatması gönder (24-48-72 saat)"},
        {"action": "collect_school_report", "description": "Okul raporu iste"},
        {"action": "collect_previous_records", "description": "Önceki tıbbi kayıtları topla"},
    ],
    JourneyStage.KLINIK_DEGERLENDIRME: [
        {"action": "prepare_session", "description": "Seans hazırlığı (form özetini hazırla)"},
        {"action": "start_recording", "description": "Ses kaydını başlat"},
        {"action": "risk_screening", "description": "Risk taraması yap"},
    ],
    JourneyStage.TANI: [
        {"action": "generate_clinical_note", "description": "Klinik not oluştur"},
        {"action": "suggest_diagnosis", "description": "DSM-5 tanı önerisi"},
        {"action": "create_treatment_plan", "description": "Tedavi planı oluştur"},
        {"action": "send_parent_summary", "description": "Ebeveyn özeti gönder"},
    ],
    JourneyStage.TEDAVI: [
        {"action": "medication_reminder", "description": "İlaç hatırlatması ayarla"},
        {"action": "schedule_followup", "description": "Kontrol randevusu planla"},
        {"action": "track_progress", "description": "İlerlemeyi takip et"},
        {"action": "send_homework", "description": "Ev ödevi/egzersiz gönder"},
    ],
    JourneyStage.IZLEM: [
        {"action": "schedule_control", "description": "Kontrol randevusu hatırlat"},
        {"action": "reassess_risk", "description": "Risk yeniden değerlendir"},
        {"action": "update_treatment_plan", "description": "Tedavi planını güncelle"},
        {"action": "score_scales", "description": "Ölçek uygula ve skorla"},
    ],
    JourneyStage.SONLANDIRMA: [
        {"action": "final_assessment", "description": "Son değerlendirme"},
        {"action": "discharge_summary", "description": "Epikriz hazırla"},
        {"action": "send_discharge_info", "description": "Taburcu bilgilendirmesi"},
    ],
}

# Aşama geçiş kuralları
VALID_TRANSITIONS: dict[JourneyStage, list[JourneyStage]] = {
    JourneyStage.BASVURU: [JourneyStage.TRIYAJ],
    JourneyStage.TRIYAJ: [JourneyStage.ON_DEGERLENDIRME, JourneyStage.KLINIK_DEGERLENDIRME],
    JourneyStage.ON_DEGERLENDIRME: [JourneyStage.KLINIK_DEGERLENDIRME],
    JourneyStage.KLINIK_DEGERLENDIRME: [JourneyStage.TANI, JourneyStage.ON_DEGERLENDIRME],
    JourneyStage.TANI: [JourneyStage.TEDAVI],
    JourneyStage.TEDAVI: [JourneyStage.IZLEM, JourneyStage.SONLANDIRMA],
    JourneyStage.IZLEM: [JourneyStage.TEDAVI, JourneyStage.SONLANDIRMA, JourneyStage.PASIF],
    JourneyStage.SONLANDIRMA: [JourneyStage.PASIF, JourneyStage.BASVURU],
    JourneyStage.PASIF: [JourneyStage.BASVURU],
}


class JourneyManager:
    """Hasta yolculuğu yöneticisi."""

    def create_journey(
        self,
        patient_id: str,
        patient_name: str,
        priority: Priority = Priority.ROUTINE,
    ) -> PatientJourney:
        """Yeni hasta yolculuğu başlatır."""
        journey = PatientJourney(
            patient_id=patient_id,
            patient_name=patient_name,
            current_stage=JourneyStage.BASVURU,
            priority=priority,
        )

        journey.events.append(JourneyEvent(
            stage=JourneyStage.BASVURU,
            timestamp=datetime.now(),
            action="journey_started",
            details=f"Hasta yolculuğu başlatıldı. Öncelik: {priority.value}",
        ))

        journey.next_actions = [
            a["description"] for a in STAGE_AUTOMATIONS.get(JourneyStage.BASVURU, [])
        ]

        logger.info("Yeni hasta yolculuğu: %s (%s)", patient_name, priority.value)
        return journey

    def advance_stage(
        self,
        journey: PatientJourney,
        new_stage: JourneyStage,
        details: str = "",
    ) -> PatientJourney:
        """Hasta yolculuğunu bir sonraki aşamaya geçirir."""
        valid_next = VALID_TRANSITIONS.get(journey.current_stage, [])
        if new_stage not in valid_next:
            raise ValueError(
                f"Geçersiz aşama geçişi: {journey.current_stage.value} -> {new_stage.value}. "
                f"İzin verilen: {[s.value for s in valid_next]}"
            )

        journey.events.append(JourneyEvent(
            stage=new_stage,
            timestamp=datetime.now(),
            action="stage_transition",
            details=details or f"{journey.current_stage.value} -> {new_stage.value}",
        ))

        journey.current_stage = new_stage
        journey.last_updated = datetime.now()

        # Yeni aşamanın otomatik aksiyonlarını belirle
        journey.next_actions = [
            a["description"] for a in STAGE_AUTOMATIONS.get(new_stage, [])
        ]

        logger.info(
            "Aşama geçişi: %s -> %s (%s)",
            journey.patient_name, new_stage.value, details,
        )
        return journey

    def get_pending_actions(self, journey: PatientJourney) -> list[dict]:
        """Mevcut aşamadaki bekleyen otomatik aksiyonları döndürür."""
        automations = STAGE_AUTOMATIONS.get(journey.current_stage, [])
        completed_actions = {e.action for e in journey.events}
        return [a for a in automations if a["action"] not in completed_actions]

    def get_overdue_patients(
        self,
        journeys: list[PatientJourney],
        days_threshold: int = 14,
    ) -> list[PatientJourney]:
        """Takibi gecikmiş hastaları döndürür."""
        threshold = datetime.now() - timedelta(days=days_threshold)
        overdue = []
        for j in journeys:
            if j.current_stage not in (JourneyStage.SONLANDIRMA, JourneyStage.PASIF):
                if j.last_updated < threshold:
                    overdue.append(j)
        return overdue

    def get_stage_summary(self, journeys: list[PatientJourney]) -> dict[str, int]:
        """Aşamalara göre hasta dağılımını döndürür."""
        summary = {}
        for j in journeys:
            stage_name = j.current_stage.value
            summary[stage_name] = summary.get(stage_name, 0) + 1
        return summary
