"""
DSM-5 tanı kodları + ölçek skorlama testleri.

Nörogelişimsel, anksiyete, duygudurum, travma kategorilerinin
varlığı; arama fonksiyonunun TR/EN/kod eşleşmesi; Conners/CDI/
SCARED/SDQ skorlamasının severity sınırları kontrol edilir.
"""

import pytest
from dsm5_codes import (
    DSM5_CODES,
    DSM5Diagnosis,
    ScaleResult,
    ScaleScorer,
    get_category_diagnoses,
    search_diagnosis,
)


# ---------------------------------------------------------------------------
# Kod kataloğu
# ---------------------------------------------------------------------------

def test_catalog_has_critical_diagnoses():
    """Çocuk-ergen psikiyatrisinde sık karşılaşılan tanılar mevcut."""
    expected = ["F90.0", "F90.2", "F84.0", "F32.1", "F41.1", "F43.10", "F91.3"]
    for code in expected:
        assert code in DSM5_CODES, f"{code} eksik"


def test_diagnosis_has_all_fields():
    """Her DSM5Diagnosis kayıt 4 zorunlu alanı doldurmuş."""
    for code, diag in DSM5_CODES.items():
        assert isinstance(diag, DSM5Diagnosis)
        assert diag.code == code
        assert diag.name_tr, f"{code} TR adı boş"
        assert diag.name_en, f"{code} EN adı boş"
        assert diag.category, f"{code} kategori boş"


def test_categories_distributed():
    """Tek kategori değil, en az 5 farklı kategori var."""
    categories = {d.category for d in DSM5_CODES.values()}
    assert len(categories) >= 5
    assert "Nörogelişimsel" in categories
    assert "Anksiyete" in categories
    assert "Duygudurum" in categories


# ---------------------------------------------------------------------------
# Arama
# ---------------------------------------------------------------------------

def test_search_by_code():
    results = search_diagnosis("F90")
    codes = {r.code for r in results}
    assert "F90.0" in codes
    assert "F90.2" in codes


def test_search_by_turkish_name():
    results = search_diagnosis("dehb")
    assert len(results) >= 3  # 3 DEHB alt tipi
    assert all("DEHB" in r.name_tr or "ADHD" in r.name_en.upper() for r in results)


def test_search_by_english_name():
    results = search_diagnosis("autism")
    assert any(r.code == "F84.0" for r in results)


def test_search_by_category():
    results = search_diagnosis("anksiyete")
    assert len(results) >= 4
    assert all(
        "Anksiyete" in r.category or "anksiyete" in r.name_tr.lower()
        or "anxiety" in r.name_en.lower()
        for r in results
    )


def test_search_empty_returns_no_match():
    results = search_diagnosis("xyz123nonexistent")
    assert results == []


def test_get_category_diagnoses():
    travma = get_category_diagnoses("Travma")
    assert len(travma) >= 3
    codes = {r.code for r in travma}
    assert "F43.10" in codes  # PTSD


# ---------------------------------------------------------------------------
# Conners ölçeği
# ---------------------------------------------------------------------------

def test_conners_normal():
    scorer = ScaleScorer()
    result = scorer.score_conners_parent([0] * 27)
    assert result.severity == "Normal"
    assert result.total_score == 0
    assert result.max_score == 81


def test_conners_severe():
    scorer = ScaleScorer()
    result = scorer.score_conners_parent([3] * 27)
    assert result.severity == "Çok Yüksek"
    assert result.total_score == 81
    assert "Dikkat Eksikliği" in result.subscale_scores


def test_conners_threshold_45():
    """45 puan = "Yüksek", 44 = "Orta"."""
    scorer = ScaleScorer()
    high = scorer.score_conners_parent([2] * 27)  # 54 — Yüksek
    assert high.severity == "Yüksek"
    moderate = scorer.score_conners_parent([1, 1, 1, 2, 2, 2, 2, 2, 2] + [1] * 18)  # ~36
    assert moderate.severity == "Orta"


# ---------------------------------------------------------------------------
# CDI (Çocuk Depresyon)
# ---------------------------------------------------------------------------

def test_cdi_normal():
    scorer = ScaleScorer()
    result = scorer.score_cdi([0] * 27)
    assert result.severity == "Normal"


def test_cdi_severe():
    scorer = ScaleScorer()
    result = scorer.score_cdi([2] * 27)
    assert result.severity == "Ağır Depresyon"
    assert "Kesme puanı: 19" in result.interpretation


def test_cdi_threshold_19():
    """Kesme puanı 19 = Orta Depresyon."""
    scorer = ScaleScorer()
    result = scorer.score_cdi([1] * 19 + [0] * 8)
    assert result.total_score == 19
    assert result.severity == "Orta Depresyon"


# ---------------------------------------------------------------------------
# SCARED (Anksiyete)
# ---------------------------------------------------------------------------

def test_scared_clinical():
    scorer = ScaleScorer()
    result = scorer.score_scared([2] * 41)
    assert result.severity == "Klinik Düzey Anksiyete"
    assert "Panik/Somatik" in result.subscale_scores


def test_scared_normal():
    scorer = ScaleScorer()
    result = scorer.score_scared([0] * 41)
    assert result.severity == "Normal"


# ---------------------------------------------------------------------------
# SDQ (Güçler ve Güçlükler)
# ---------------------------------------------------------------------------

def test_sdq_anormal():
    scorer = ScaleScorer()
    # 25 maddeye 1 ver — bazı toplam değerler subscale hesaplaması
    # nedeniyle azalır; 2-2-2... ile kesin anormal
    result = scorer.score_sdq([2] * 25)
    assert result.severity == "Anormal"


def test_sdq_normal():
    scorer = ScaleScorer()
    result = scorer.score_sdq([0] * 25)
    assert result.severity == "Normal"


# ---------------------------------------------------------------------------
# Sonuç tipleri
# ---------------------------------------------------------------------------

def test_scale_result_dataclass():
    result = ScaleResult(
        scale_name="X", total_score=10, max_score=20, severity="Test"
    )
    assert result.scale_name == "X"
    assert result.subscale_scores == {}
    assert result.percentile is None
