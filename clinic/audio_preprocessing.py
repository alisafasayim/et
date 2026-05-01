"""
Ses Ön-İşleme Pipeline
=======================
M4A/MP3 -> WAV dönüşüm, normalizasyon, gürültü azaltma, kalite kontrol.
Doküman: ses_kayit_sistemi_tasarimi.md
"""

import os
import logging
import tempfile
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AudioQuality(Enum):
    EXCELLENT = "excellent"  # SNR > 30dB
    GOOD = "good"            # SNR 20-30dB
    FAIR = "fair"            # SNR 10-20dB
    POOR = "poor"            # SNR < 10dB


@dataclass
class AudioAnalysis:
    """Ses dosyası analiz sonucu."""
    file_path: str
    duration_seconds: float
    sample_rate: int
    channels: int
    bit_depth: int
    format: str
    file_size_mb: float
    quality: AudioQuality
    snr_db: float
    peak_db: float
    mean_db: float
    silence_ratio: float  # sessizlik oranı (0-1)
    needs_preprocessing: bool
    issues: list[str]


class AudioPreprocessor:
    """Ses dosyası ön-işleme motoru.

    Pipeline: M4A/MP3 -> WAV (PCM 16-bit, 16kHz) -> Normalizasyon
              -> Gürültü Azaltma -> Kalite Kontrol
    """

    TARGET_SAMPLE_RATE = 16000
    TARGET_BIT_DEPTH = 16
    TARGET_CHANNELS = 1
    SILENCE_THRESHOLD_DB = -40
    MIN_QUALITY_SNR = 10  # dB

    def analyze(self, audio_path: str) -> AudioAnalysis:
        """Ses dosyasını analiz eder (dönüştürmeden)."""
        from pydub import AudioSegment
        from pydub.silence import detect_silence

        path = Path(audio_path)
        audio = AudioSegment.from_file(str(path))

        duration = len(audio) / 1000.0
        file_size = path.stat().st_size / (1024 * 1024)

        # Sessizlik analizi
        silent_ranges = detect_silence(
            audio, min_silence_len=1000, silence_thresh=self.SILENCE_THRESHOLD_DB
        )
        total_silence_ms = sum(end - start for start, end in silent_ranges)
        silence_ratio = total_silence_ms / len(audio) if len(audio) > 0 else 0

        # Peak ve ortalama ses seviyesi
        peak_db = audio.max_dBFS
        mean_db = audio.dBFS

        # Tahmini SNR (sinyal-gürültü oranı)
        snr_db = abs(mean_db - self.SILENCE_THRESHOLD_DB)

        # Kalite değerlendirmesi
        if snr_db > 30:
            quality = AudioQuality.EXCELLENT
        elif snr_db > 20:
            quality = AudioQuality.GOOD
        elif snr_db > 10:
            quality = AudioQuality.FAIR
        else:
            quality = AudioQuality.POOR

        # Sorun tespiti
        issues = []
        needs_preprocessing = False

        if audio.frame_rate != self.TARGET_SAMPLE_RATE:
            issues.append(f"Örnek hızı {audio.frame_rate}Hz (hedef: {self.TARGET_SAMPLE_RATE}Hz)")
            needs_preprocessing = True

        if audio.channels != self.TARGET_CHANNELS:
            issues.append(f"Kanal sayısı {audio.channels} (hedef: mono)")
            needs_preprocessing = True

        if path.suffix.lower() != ".wav":
            issues.append(f"Format {path.suffix} (hedef: WAV)")
            needs_preprocessing = True

        if quality in (AudioQuality.POOR,):
            issues.append(f"Düşük kalite: SNR {snr_db:.1f}dB")

        if silence_ratio > 0.5:
            issues.append(f"Yüksek sessizlik oranı: {silence_ratio:.0%}")

        if peak_db > -1:
            issues.append("Ses kırpılması (clipping) riski")

        return AudioAnalysis(
            file_path=audio_path,
            duration_seconds=duration,
            sample_rate=audio.frame_rate,
            channels=audio.channels,
            bit_depth=audio.sample_width * 8,
            format=path.suffix.lower().lstrip("."),
            file_size_mb=file_size,
            quality=quality,
            snr_db=snr_db,
            peak_db=peak_db,
            mean_db=mean_db,
            silence_ratio=silence_ratio,
            needs_preprocessing=needs_preprocessing,
            issues=issues,
        )

    def preprocess(self, audio_path: str, output_dir: str | None = None) -> str:
        """Tam ön-işleme pipeline: dönüşüm + normalizasyon + gürültü azaltma.

        Returns:
            İşlenmiş WAV dosyasının yolu.
        """
        from pydub import AudioSegment
        from pydub.effects import normalize, compress_dynamic_range

        path = Path(audio_path)
        logger.info("Ön-işleme başlıyor: %s", path.name)

        # 1. Dosyayı yükle
        audio = AudioSegment.from_file(str(path))
        logger.debug("Yüklendi: %dHz, %d kanal, %.0f sn", audio.frame_rate, audio.channels, len(audio) / 1000)

        # 2. Mono'ya çevir
        if audio.channels > 1:
            audio = audio.set_channels(self.TARGET_CHANNELS)
            logger.debug("Mono'ya dönüştürüldü.")

        # 3. Örnek hızını ayarla
        if audio.frame_rate != self.TARGET_SAMPLE_RATE:
            audio = audio.set_frame_rate(self.TARGET_SAMPLE_RATE)
            logger.debug("Örnek hızı %dHz olarak ayarlandı.", self.TARGET_SAMPLE_RATE)

        # 4. Bit derinliği
        audio = audio.set_sample_width(self.TARGET_BIT_DEPTH // 8)

        # 5. Normalizasyon
        audio = normalize(audio)
        logger.debug("Normalizasyon uygulandı.")

        # 6. Dinamik aralık sıkıştırma (fısıltı-bağırma dengelemesi)
        audio = compress_dynamic_range(audio, threshold=-20.0, ratio=4.0, attack=5.0, release=50.0)
        logger.debug("Dinamik aralık sıkıştırma uygulandı.")

        # 7. Başlangıç/bitiş sessizliğini kırp
        audio = self._trim_silence(audio)

        # 8. Çıktı dosyası
        if output_dir:
            out_dir = Path(output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
        else:
            out_dir = path.parent

        output_path = str(out_dir / f"{path.stem}_processed.wav")
        audio.export(output_path, format="wav")

        logger.info(
            "Ön-işleme tamamlandı: %s (%.1f MB -> %.1f MB)",
            Path(output_path).name,
            path.stat().st_size / 1024 / 1024,
            Path(output_path).stat().st_size / 1024 / 1024,
        )
        return output_path

    def _trim_silence(self, audio, silence_thresh: int = -40, chunk_size: int = 100) -> "AudioSegment":
        """Başlangıç ve bitişteki sessizliği kırpar."""
        from pydub.silence import detect_leading_silence

        start_trim = detect_leading_silence(audio, silence_threshold=silence_thresh, chunk_size=chunk_size)
        end_trim = detect_leading_silence(audio.reverse(), silence_threshold=silence_thresh, chunk_size=chunk_size)

        # En az 200ms bırak
        start_trim = max(0, start_trim - 200)
        end_trim = max(0, end_trim - 200)

        trimmed = audio[start_trim:len(audio) - end_trim]
        if len(trimmed) < 1000:  # 1 saniyeden kısa ise orijinali döndür
            return audio
        return trimmed

    def batch_preprocess(self, audio_dir: str, output_dir: str | None = None) -> list[dict]:
        """Dizindeki tüm ses dosyalarını toplu işler."""
        dir_path = Path(audio_dir)
        extensions = {".m4a", ".mp3", ".wav", ".ogg", ".flac"}
        files = [f for f in dir_path.iterdir() if f.suffix.lower() in extensions]

        results = []
        for f in sorted(files):
            try:
                analysis = self.analyze(str(f))
                if analysis.needs_preprocessing:
                    output = self.preprocess(str(f), output_dir)
                    results.append({"file": str(f), "output": output, "status": "processed", "quality": analysis.quality.value})
                else:
                    results.append({"file": str(f), "output": str(f), "status": "skipped", "quality": analysis.quality.value})
            except Exception as e:
                logger.error("İşleme hatası (%s): %s", f.name, e)
                results.append({"file": str(f), "output": None, "status": "error", "error": str(e)})

        return results
