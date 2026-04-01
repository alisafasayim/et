"""
Ses Transkripsiyon Modülü
=========================
m4a/mp3 dosyalarını metne çevirir.
OpenAI Whisper API veya local faster-whisper destekler.
"""

import os
import logging
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from clinic_automation.config.settings import TranscriptionConfig

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionSegment:
    """Transkripsiyon segmenti (zaman damgalı)."""
    start: float  # saniye
    end: float
    text: str
    speaker: str = ""  # diarization ile doldurulur


@dataclass
class TranscriptionResult:
    """Tam transkripsiyon sonucu."""
    audio_path: str
    language: str
    duration_seconds: float
    full_text: str
    segments: list[TranscriptionSegment] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class AudioTranscriber:
    """Ses dosyası transkripsiyon motoru."""

    def __init__(self, config: TranscriptionConfig):
        self.config = config

    def transcribe(self, audio_path: str) -> TranscriptionResult:
        """Ses dosyasını transkript eder."""
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Ses dosyası bulunamadı: {audio_path}")

        logger.info("Transkripsiyon başlıyor: %s", path.name)

        # Dosya süresini al
        duration = self._get_duration(audio_path)

        if self.config.provider == "openai":
            result = self._transcribe_openai(audio_path, duration)
        else:
            result = self._transcribe_local(audio_path, duration)

        logger.info(
            "Transkripsiyon tamamlandı: %s (%.0f sn, %d segment)",
            path.name, duration, len(result.segments),
        )
        return result

    def _transcribe_openai(self, audio_path: str, duration: float) -> TranscriptionResult:
        """OpenAI Whisper API ile transkript."""
        from openai import OpenAI

        client = OpenAI(api_key=self.config.openai_api_key)
        path = Path(audio_path)

        # m4a dosyasını gerekirse mp3'e çevir (API uyumluluğu)
        process_path = audio_path
        if path.suffix.lower() == ".m4a":
            process_path = self._convert_to_mp3(audio_path)

        # 25MB sınırı kontrol
        file_size = os.path.getsize(process_path)
        if file_size > 25 * 1024 * 1024:
            return self._transcribe_openai_chunked(process_path, duration)

        with open(process_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model=self.config.whisper_model,
                file=audio_file,
                language=self.config.language,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        segments = []
        for seg in getattr(response, "segments", []):
            segments.append(TranscriptionSegment(
                start=seg.get("start", 0),
                end=seg.get("end", 0),
                text=seg.get("text", "").strip(),
            ))

        # Geçici dosyayı temizle
        if process_path != audio_path:
            os.unlink(process_path)

        return TranscriptionResult(
            audio_path=audio_path,
            language=self.config.language,
            duration_seconds=duration,
            full_text=response.text,
            segments=segments,
            metadata={"provider": "openai", "model": self.config.whisper_model},
        )

    def _transcribe_openai_chunked(self, audio_path: str, total_duration: float) -> TranscriptionResult:
        """Büyük dosyaları parçalara bölerek transkript eder."""
        from pydub import AudioSegment
        from openai import OpenAI

        client = OpenAI(api_key=self.config.openai_api_key)
        audio = AudioSegment.from_file(audio_path)

        chunk_length_ms = 10 * 60 * 1000  # 10 dakika
        chunks = [audio[i:i + chunk_length_ms] for i in range(0, len(audio), chunk_length_ms)]

        all_segments = []
        all_text = []
        time_offset = 0.0

        for i, chunk in enumerate(chunks):
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                chunk.export(tmp.name, format="mp3")
                with open(tmp.name, "rb") as f:
                    response = client.audio.transcriptions.create(
                        model=self.config.whisper_model,
                        file=f,
                        language=self.config.language,
                        response_format="verbose_json",
                        timestamp_granularities=["segment"],
                    )
                os.unlink(tmp.name)

            all_text.append(response.text)
            for seg in getattr(response, "segments", []):
                all_segments.append(TranscriptionSegment(
                    start=seg.get("start", 0) + time_offset,
                    end=seg.get("end", 0) + time_offset,
                    text=seg.get("text", "").strip(),
                ))

            time_offset += chunk_length_ms / 1000.0
            logger.info("Parça %d/%d transkript edildi.", i + 1, len(chunks))

        return TranscriptionResult(
            audio_path=audio_path,
            language=self.config.language,
            duration_seconds=total_duration,
            full_text=" ".join(all_text),
            segments=all_segments,
            metadata={"provider": "openai", "model": self.config.whisper_model, "chunks": len(chunks)},
        )

    def _transcribe_local(self, audio_path: str, duration: float) -> TranscriptionResult:
        """Local faster-whisper ile transkript."""
        from faster_whisper import WhisperModel

        model = WhisperModel(
            self.config.local_model_size,
            device="auto",
            compute_type="auto",
        )

        segments_gen, info = model.transcribe(
            audio_path,
            language=self.config.language,
            beam_size=5,
            vad_filter=True,
        )

        segments = []
        full_text_parts = []
        for seg in segments_gen:
            segments.append(TranscriptionSegment(
                start=seg.start,
                end=seg.end,
                text=seg.text.strip(),
            ))
            full_text_parts.append(seg.text.strip())

        return TranscriptionResult(
            audio_path=audio_path,
            language=self.config.language,
            duration_seconds=duration,
            full_text=" ".join(full_text_parts),
            segments=segments,
            metadata={
                "provider": "local",
                "model": self.config.local_model_size,
                "detected_language": info.language,
                "language_probability": info.language_probability,
            },
        )

    @staticmethod
    def _get_duration(audio_path: str) -> float:
        """Ses dosyasının süresini saniye olarak döndürür."""
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(audio_path)
            return len(audio) / 1000.0
        except Exception:
            logger.warning("Süre alınamadı, tahmini değer kullanılacak: %s", audio_path)
            # Dosya boyutundan tahmini süre (128kbps varsayımı)
            size_bytes = os.path.getsize(audio_path)
            return size_bytes / (128 * 1024 / 8)

    @staticmethod
    def _convert_to_mp3(audio_path: str) -> str:
        """m4a dosyasını mp3'e çevirir."""
        from pydub import AudioSegment
        audio = AudioSegment.from_file(audio_path, format="m4a")
        mp3_path = tempfile.mktemp(suffix=".mp3")
        audio.export(mp3_path, format="mp3", bitrate="128k")
        return mp3_path

    def get_audio_files(self, directory: str | None = None) -> list[Path]:
        """Ses dizinindeki tüm ses dosyalarını listeler."""
        audio_dir = Path(directory or self.config.audio_dir)
        if not audio_dir.exists():
            logger.warning("Ses dizini bulunamadı: %s", audio_dir)
            return []

        extensions = {".m4a", ".mp3", ".wav", ".ogg", ".flac"}
        files = [f for f in audio_dir.iterdir() if f.suffix.lower() in extensions]
        return sorted(files, key=lambda f: f.stat().st_mtime)
