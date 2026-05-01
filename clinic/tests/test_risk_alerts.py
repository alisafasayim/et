"""risk_alerts.detect_risk testleri.

KRİTİK: bu modül hayati riskli hasta sözlerini doktora ileteceği için
sahte negatif (risk var ama tespit edilmedi) bir kabuslık. Geniş
test kapsamı şart.
"""

import pytest

from risk_alerts import detect_risk


def _soap(risk_text: str = "", history: str = "", chief: str = "", plan: str = "") -> dict:
    return {
        "patient_name": "Test",
        "appointment_id": "evt-1",
        "soap": {
            "subjective": {
                "chief_complaint": chief,
                "history_of_present_illness": history,
            },
            "objective": {},
            "assessment": {"risk_assessment": risk_text},
            "plan": {"follow_up": plan},
        },
    }


def test_no_risk_returns_none():
    result = detect_risk(_soap(risk_text="Risk düşük, koruyucu faktörler mevcut."))
    assert result["level"] == "none"
    assert result["matched"] == []


@pytest.mark.parametrize(
    "phrase",
    [
        "İntihar planı var, hap içeceğini ifade etti",
        "Bilek kesme girişimi anamnezde mevcut",
        "Kendini öldürmek istediğini söylüyor",
        "Suicidal plan reported",
        "self-harm geçmişi var",
        "İp ile asma niyetinden bahsetti",
    ],
)
def test_critical_phrases_detected(phrase):
    result = detect_risk(_soap(risk_text=phrase))
    assert result["level"] == "critical", f"Critical kaçırıldı: {phrase}"


@pytest.mark.parametrize(
    "phrase",
    [
        "İntihar düşünceleri var ama plan yok",
        "Ölüm fikri zaman zaman geliyormuş",
        "Yaşamak istemediğini söylüyor",
        "Kendine zarar verme dürtüsü var",
        "Suicidal ideation reported",
        "Hayatımın anlamı yok diyor",
    ],
)
def test_high_phrases_detected(phrase):
    result = detect_risk(_soap(risk_text=phrase))
    assert result["level"] == "high", f"High kaçırıldı: {phrase}"


def test_critical_overrides_high_when_both_present():
    """Aynı SOAP'ta hem high hem critical varsa critical kazanır."""
    result = detect_risk(
        _soap(
            risk_text="ölüm düşünceleri var",  # high
            history="hap içme planı yapmış",   # critical
        )
    )
    assert result["level"] == "critical"


def test_scans_all_soap_fields_not_just_risk_assessment():
    """Risk anahtar kelimesi başka bir alana yazılmış olabilir."""
    result = detect_risk(
        _soap(chief="Kendine zarar verme dürtüleri", risk_text="—")
    )
    assert result["level"] == "high"


def test_snippets_include_field_path():
    result = detect_risk(_soap(risk_text="intihar planı"))
    assert any("assessment.risk_assessment" in s["path"] for s in result["snippets"])


def test_empty_soap_no_risk():
    result = detect_risk({})
    assert result["level"] == "none"


def test_case_insensitive():
    result = detect_risk(_soap(risk_text="İNTİHAR PLANI"))
    assert result["level"] == "critical"
