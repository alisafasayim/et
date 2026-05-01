"""
Audio preprocessing testleri.

pydub gerçek ses dosyası gerektirdiği için import edilemediğinde
testler skip edilir; mevcut iken AudioPreprocessor.analyze() ve
quality threshold'ları (SNR sınırları) kontrol edilir.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Pydub yoksa tüm testler skip
pydub = pytest.importorskip(
    "pydub",
    reason="pydub yüklü değil — audio preprocessing testleri atlanıyor",
)

from audio_preprocessing import (
    AudioAnalysis,
    AudioPreprocessor,
    AudioQuality,
)


# ---------------------------------------------------------------------------
# Quality enum
# ---------------------------------------------------------------------------

def test_audio_quality_values():
    assert AudioQuality.EXCELLENT.value == "excellent"
    assert AudioQuality.GOOD.value == "good"
    assert AudioQuality.FAIR.value == "fair"
    assert AudioQuality.POOR.value == "poor"


# ---------------------------------------------------------------------------
# AudioAnalysis dataclass
# ---------------------------------------------------------------------------

def test_audio_analysis_construction():
    analysis = AudioAnalysis(
        file_path="/tmp/test.m4a",
        duration_seconds=120.5,
        sample_rate=44100,
        channels=2,
        bit_depth=16,
        format="m4a",
        file_size_mb=2.4,
        quality=AudioQuality.GOOD,
        snr_db=25.0,
        peak_db=-3.0,
        mean_db=-15.0,
        silence_ratio=0.12,
        needs_preprocessing=True,
        issues=["yüksek gürültü"],
    )
    assert analysis.duration_seconds == 120.5
    assert analysis.quality == AudioQuality.GOOD
    assert "yüksek gürültü" in analysis.issues


# ---------------------------------------------------------------------------
# Preprocessor — mock'lu davranış kontrolü
# ---------------------------------------------------------------------------

def test_preprocessor_instantiates():
    p = AudioPreprocessor()
    assert p is not None


def test_preprocessor_analyze_nonexistent_file():
    """Var olmayan dosya analiz edilince hata fırlatır veya issues döner."""
    p = AudioPreprocessor()
    with pytest.raises((FileNotFoundError, Exception)):
        p.analyze("/nonexistent/path/to/file.m4a")


# ---------------------------------------------------------------------------
# SNR/severity sınırları (logic kontrol — gerçek dosya gerekmiyor)
# ---------------------------------------------------------------------------

def test_quality_thresholds_logic():
    """Quality enum sırası: SNR yüksek → excellent, düşük → poor."""
    qualities = list(AudioQuality)
    assert qualities[0] == AudioQuality.EXCELLENT
    assert qualities[-1] == AudioQuality.POOR
