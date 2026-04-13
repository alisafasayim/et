"""
Risk Değerlendirme Modülü
=========================
4 seviyeli risk değerlendirme sistemi.
Doküman: cocuk_ergen_psikiyatri_klinik_workflow.md

Seviyeler:
- DUSUK: Rutin takip yeterli
- ORTA: Yakın takip, güvenlik planı
- YUKSEK: Acil müdahale gerekebilir, aile bilgilendirilmeli
- KRITIK: Acil psikiyatrik değerlendirme, hastane yönlendirme
"""

import re
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

logger = logging.getLogger(__name__)


class RiskLevel(IntEnum):
    LOW = 1
    MODERATE = 2
    HIGH = 3
    CRITICAL = 4


RISK_LABELS_TR = {
    RiskLevel.LOW: "Düşük",
    RiskLevel.MODERATE: "Orta",
    RiskLevel.HIGH: "Yüksek",
    RiskLevel.CRITICAL: "Kritik",
}


@dataclass
class RiskFactor:
    """Tekil risk faktörü."""
    category: str       # ozkiyim, oz_zarar, baskasina_zarar, ihmal_istismar
    description: str
    severity: RiskLevel
    source: str = ""    # Tespit kaynağı (transkript, form, klinisyen)
    evidence: str = ""  # Kanıt metni


@dataclass
class RiskAssessment:
    """Tam risk değerlendirmesi sonucu."""
    patient_name: str
    assessment_date: str
    overall_level: RiskLevel
    factors: list[RiskFactor] = field(default_factory=list)
    protective_factors: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)
    safety_plan_needed: bool = False
    emergency_contact_notified: bool = False
    notes: str = ""

    @property
    def overall_level_tr(self) -> str:
        return RISK_LABELS_TR.get(self.overall_level, "Bilinmiyor")


# Anahtar kelime tabanlı risk taraması
RISK_KEYWORDS = {
    "ozkiyim": {
        RiskLevel.CRITICAL: [
            r"intihar\s*(?:planı|girişimi|teşebbüs)",
            r"kendimi?\s*öldür",
            r"yaşamak\s*istemi",
            r"intihar\s*(?:not|mektup)",
            r"hap.*(?:içtim|yuttum|aldım)",
        ],
        RiskLevel.HIGH: [
            r"intihar.*düşün",
            r"ölmek\s*ist",
            r"hayat.*anlam",
            r"kendime?\s*(?:zarar|kıy)",
            r"ölsem\s*(?:daha|keşke)",
        ],
        RiskLevel.MODERATE: [
            r"ölüm.*düşün",
            r"yaşam.*değ(?:er|me)",
            r"umutsuz",
            r"çıkış\s*(?:yol|yok)",
        ],
    },
    "oz_zarar": {
        RiskLevel.HIGH: [
            r"(?:kol|bacak|bilek).*kes",
            r"kendimi?\s*yak",
            r"jilet",
            r"yara\s*iz",
            r"self.?harm",
        ],
        RiskLevel.MODERATE: [
            r"kendime?\s*(?:vur|zarar)",
            r"acı.*(?:hisset|verme)",
            r"(?:saç|kıl).*(?:yol|çek|kopat)",
            r"kafamı?\s*(?:vur|duvar)",
        ],
    },
    "baskasina_zarar": {
        RiskLevel.HIGH: [
            r"öldür.*(?:ist|ece|düşün)",
            r"(?:bıçak|silah).*(?:al|getir|kullan)",
            r"ateş.*(?:aç|et)",
            r"(?:anne|baba|kardeş).*(?:vur|öldür)",
        ],
        RiskLevel.MODERATE: [
            r"(?:kavga|dövüş|vur|tekme)",
            r"zarar\s*ver.*(?:ist|ece)",
            r"(?:sinir|öfke).*kontrol",
            r"yangın.*çıkar",
        ],
    },
    "ihmal_istismar": {
        RiskLevel.CRITICAL: [
            r"(?:cinsel|fiziksel)\s*istismar",
            r"tecavüz",
            r"(?:dokunma|elleme).*uygunsuz",
            r"çocuk\s*(?:istismar|ihmal)",
        ],
        RiskLevel.HIGH: [
            r"(?:dövül|dayak|şiddet).*(?:ev|anne|baba)",
            r"(?:ev|anne|baba).*(?:dövül|dayak|şiddet)",
            r"fiziksel\s+şiddet",
            r"aç\s*bırak",
            r"(?:eve|odaya)\s*(?:kilit|kapat)",
            r"korku.*(?:anne|baba)",
        ],
    },
}

# Koruyucu faktörler
PROTECTIVE_KEYWORDS = [
    (r"aile\s*(?:destek|ilgi|yanında)", "Aile desteği mevcut"),
    (r"arkadaş.*(?:var|iyi|destek)", "Arkadaş ilişkileri olumlu"),
    (r"okul.*(?:sev|başarılı|iyi)", "Okul uyumu iyi"),
    (r"tedavi.*(?:devam|uyum|düzenli)", "Tedaviye uyum iyi"),
    (r"ilaç.*(?:düzenli|kullan)", "İlaç uyumu var"),
    (r"hobi|spor|müzik|resim", "Aktivite/hobi var"),
    (r"gelecek.*(?:plan|hedef|iste)", "Gelecek planları var"),
]

# Risk seviyesine göre önerilen aksiyonlar
RISK_ACTIONS = {
    RiskLevel.LOW: [
        "Rutin takip planına devam",
        "Bir sonraki seansta tekrar değerlendir",
    ],
    RiskLevel.MODERATE: [
        "Takip sıklığını artır",
        "Güvenlik planı oluştur/güncelle",
        "Aileyi bilgilendir",
        "Acil iletişim bilgilerini doğrula",
    ],
    RiskLevel.HIGH: [
        "ACİL değerlendirme gerekli",
        "Aile derhal bilgilendirilmeli",
        "Güvenlik planı aktifleştirilmeli",
        "Yalnız bırakılmamalı",
        "Kesici/delici aletlere erişim engellenmeli",
        "Gerekirse hastane yönlendirmesi",
    ],
    RiskLevel.CRITICAL: [
        "ACİL PSİKİYATRİK DEĞERLENDİRME",
        "112/Acil servis yönlendirmesi",
        "Hasta güvenliği sağlanana kadar gözetim altında tutulmalı",
        "Hastane yatış değerlendirilmeli",
        "Yasal bildirim gerekliliği kontrol edilmeli",
    ],
}


class RiskAssessor:
    """Otomatik risk değerlendirme motoru."""

    def assess_from_transcript(
        self,
        patient_name: str,
        transcript: str,
        assessment_date: str,
    ) -> RiskAssessment:
        """Transkriptten otomatik risk değerlendirmesi yapar."""
        text_lower = transcript.lower()
        factors = []
        protective = []

        # Risk faktörlerini tara
        for category, levels in RISK_KEYWORDS.items():
            for level, patterns in levels.items():
                for pattern in patterns:
                    matches = re.finditer(pattern, text_lower)
                    for match in matches:
                        # Bağlam çıkar (eşleşme etrafındaki metin)
                        start = max(0, match.start() - 50)
                        end = min(len(text_lower), match.end() + 50)
                        context = transcript[start:end].strip()

                        factors.append(RiskFactor(
                            category=category,
                            description=self._get_category_label(category),
                            severity=level,
                            source="transkript",
                            evidence=f"...{context}...",
                        ))

        # Koruyucu faktörleri tara
        for pattern, description in PROTECTIVE_KEYWORDS:
            if re.search(pattern, text_lower):
                protective.append(description)

        # En yüksek risk seviyesini belirle
        if factors:
            overall = max(f.severity for f in factors)
        else:
            overall = RiskLevel.LOW

        # Duplike faktörleri kaldır (aynı kategori + aynı seviye)
        seen = set()
        unique_factors = []
        for f in factors:
            key = (f.category, f.severity)
            if key not in seen:
                seen.add(key)
                unique_factors.append(f)

        # Önerilen aksiyonlar
        actions = list(RISK_ACTIONS.get(overall, []))

        assessment = RiskAssessment(
            patient_name=patient_name,
            assessment_date=assessment_date,
            overall_level=overall,
            factors=unique_factors,
            protective_factors=protective,
            recommended_actions=actions,
            safety_plan_needed=overall >= RiskLevel.MODERATE,
            emergency_contact_notified=False,
            notes=f"Otomatik tarama: {len(unique_factors)} risk faktörü, {len(protective)} koruyucu faktör tespit edildi.",
        )

        if overall >= RiskLevel.HIGH:
            logger.warning(
                "YÜKSEK RİSK TESPİTİ: %s - Seviye: %s (%d faktör)",
                patient_name, RISK_LABELS_TR[overall], len(unique_factors),
            )

        return assessment

    def assess_from_form(
        self,
        patient_name: str,
        form_data: dict,
        assessment_date: str,
    ) -> RiskAssessment:
        """Form verilerinden risk değerlendirmesi yapar."""
        combined_text = " ".join(str(v) for v in form_data.values() if v)
        return self.assess_from_transcript(patient_name, combined_text, assessment_date)

    @staticmethod
    def _get_category_label(category: str) -> str:
        labels = {
            "ozkiyim": "Özkıyım (İntihar) Riski",
            "oz_zarar": "Kendine Zarar Verme Riski",
            "baskasina_zarar": "Başkasına Zarar Verme Riski",
            "ihmal_istismar": "İhmal/İstismar Şüphesi",
        }
        return labels.get(category, category)

    @staticmethod
    def format_risk_report(assessment: RiskAssessment) -> str:
        """Risk değerlendirmesini okunabilir rapora çevirir."""
        lines = [
            f"RİSK DEĞERLENDİRME RAPORU",
            f"Hasta: {assessment.patient_name}",
            f"Tarih: {assessment.assessment_date}",
            f"Genel Risk Seviyesi: {assessment.overall_level_tr}",
            "=" * 50,
        ]

        if assessment.factors:
            lines.append("\nTESPİT EDİLEN RİSK FAKTÖRLERİ:")
            for f in assessment.factors:
                lines.append(f"  [{RISK_LABELS_TR[f.severity]}] {f.description}")
                if f.evidence:
                    lines.append(f"    Kanıt: {f.evidence}")

        if assessment.protective_factors:
            lines.append("\nKORUYUCU FAKTÖRLER:")
            for p in assessment.protective_factors:
                lines.append(f"  + {p}")

        if assessment.recommended_actions:
            lines.append("\nÖNERİLEN AKSİYONLAR:")
            for a in assessment.recommended_actions:
                lines.append(f"  * {a}")

        if assessment.safety_plan_needed:
            lines.append("\n⚠ GÜVENLİK PLANI GEREKLİ")

        return "\n".join(lines)
