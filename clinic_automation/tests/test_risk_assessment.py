"""
Risk değerlendirme modülü için birim testleri.
"""

import pytest
from clinic_automation.modules.risk_assessment import (
    RiskAssessor,
    RiskLevel,
    RISK_LABELS_TR,
)


class TestRiskAssessor:
    def setup_method(self):
        self.assessor = RiskAssessor()
        self.today = "2024-03-15"

    def test_no_risk_clean_text(self):
        text = "Hasta bugün iyi hissediyor. Arkadaşlarıyla vakit geçiriyor. Okula devam ediyor."
        result = self.assessor.assess_from_transcript("Test Hasta", text, self.today)
        assert result.overall_level == RiskLevel.LOW

    def test_detects_suicidal_ideation(self):
        text = "Hasta ölmek istediğini söylüyor. Hayatın anlamsız geldiğini belirtiyor."
        result = self.assessor.assess_from_transcript("Test Hasta", text, self.today)
        assert result.overall_level >= RiskLevel.MODERATE

    def test_detects_critical_risk(self):
        text = "Hasta intihar planı yaptığını, hap aldığını söylüyor."
        result = self.assessor.assess_from_transcript("Test Hasta", text, self.today)
        assert result.overall_level == RiskLevel.CRITICAL

    def test_detects_self_harm(self):
        text = "Kollarında jilet izleri mevcut. Kendine zarar verdiğini ifade etti."
        result = self.assessor.assess_from_transcript("Test Hasta", text, self.today)
        assert result.overall_level >= RiskLevel.HIGH

    def test_detects_abuse_suspicion(self):
        text = "Evde babasının kendisine fiziksel şiddet uyguladığını ve dövüldüğünü anlattı."
        result = self.assessor.assess_from_transcript("Test Hasta", text, self.today)
        assert result.overall_level >= RiskLevel.HIGH

    def test_protective_factors_detected(self):
        text = "Aile desteği mevcut. Arkadaşları var. Hobisi olarak müzikle ilgileniyor."
        result = self.assessor.assess_from_transcript("Test Hasta", text, self.today)
        assert len(result.protective_factors) > 0

    def test_safety_plan_for_moderate_plus(self):
        text = "Ölüm düşünceleri var ama plan yok."
        result = self.assessor.assess_from_transcript("Test Hasta", text, self.today)
        if result.overall_level >= RiskLevel.MODERATE:
            assert result.safety_plan_needed is True

    def test_recommended_actions_populated(self):
        text = "Hasta risk altında."
        result = self.assessor.assess_from_transcript("Test Hasta", text, self.today)
        assert len(result.recommended_actions) > 0

    def test_format_report(self):
        text = "Hasta intihar düşüncelerinden bahsetti."
        result = self.assessor.assess_from_transcript("Test Hasta", text, self.today)
        report = self.assessor.format_risk_report(result)
        assert "RİSK DEĞERLENDİRME" in report
        assert result.overall_level_tr in report

    def test_risk_labels_complete(self):
        for level in RiskLevel:
            assert level in RISK_LABELS_TR
