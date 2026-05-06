#!/usr/bin/env python3
"""
Transcribe Only — Sadece Whisper transkripsiyon, AI yorum yok.

Klinik kullanım: doktor transkripti kendisi okur, raporu kendi yazar.
AI rapor üretimi devre dışı — yanıltıcı tanı + risk değerlendirmesi
problemini önler (LLM oyun/film içeriğini gerçek vaka olarak yorumluyor).

KVKK güvenli:
- Tamamen lokal işlem (faster-whisper local model)
- Dosya adındaki gerçek isim → patient_registry'ye Fernet ile şifreli
- Çıktı dosyası pseudonym (#abcd-1234) ile adlandırılır

Kullanım:
    # Tek dosya
    python scripts/transcribe_only.py audio_inbox/seans1.m4a

    # audio_inbox/ klasöründeki tüm dosyalar
    python scripts/transcribe_only.py --batch

    # Hız vs kalite seçimi
    python scripts/transcribe_only.py --batch --model small    # ~3x hızlı, doğruluk %88-92
    python scripts/transcribe_only.py --batch --model medium   # default, doğruluk %95+
    python scripts/transcribe_only.py --batch --model large-v3 # en doğru, 3x yavaş
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "audio_processed"
PROCESSED_DIR.mkdir(exist_ok=True)


def extract_name_and_date(audio_path: Path) -> tuple[str, str]:
    """
    Dosya adından isim + tarihi parse et.
    Format: 'isim soyisim DD.MM.YYYY.m4a'
    """
    stem = audio_path.stem
    date_match = re.search(r"(\d{1,2})[._-](\d{1,2})[._-](\d{4})", stem)
    if date_match:
        date_iso = f"{date_match.group(3)}-{int(date_match.group(2)):02d}-{int(date_match.group(1)):02d}"
        name_part = stem[:date_match.start()].strip(" -_")
    else:
        date_iso = datetime.now().strftime("%Y-%m-%d")
        name_part = stem.strip(" -_")
    return (name_part or "unknown_patient"), date_iso


def transcribe(audio_path: Path, model_size: str) -> str:
    """Faster-Whisper ile transkripsiyon. CPU int8."""
    from faster_whisper import WhisperModel

    print(f"{DIM}  Whisper modeli yükleniyor ({model_size})...{RESET}")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    print(f"{DIM}  Transkripsiyon başladı...{RESET}")
    t0 = time.time()
    segments, info = model.transcribe(
        str(audio_path),
        language="tr",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    text_parts = [seg.text.strip() for seg in segments]
    transcript = " ".join(text_parts)
    elapsed = time.time() - t0
    duration = getattr(info, "duration", 0)
    print(
        f"{GREEN}  ✓ Transkript hazır: {len(transcript)} karakter "
        f"(ses: {duration/60:.1f} dk, işlem: {elapsed/60:.1f} dk){RESET}"
    )
    return transcript


def process_audio(audio_path: Path, model_size: str, archive: bool = False) -> dict:
    """Tek ses dosyası → transkript (KVKK pseudonym ile çıktı)."""
    print(f"{YELLOW}► {audio_path.name}{RESET}")

    real_name, session_date = extract_name_and_date(audio_path)
    from patient_registry import get_default_registry
    from pii_crypto import short_pseudonym

    registry = get_default_registry()
    existing = registry.find_by_name(real_name)
    if existing:
        patient_uuid = existing[0]["uuid"]
    else:
        patient_uuid = registry.create_patient(full_name=real_name)
    pseudonym = short_pseudonym(patient_uuid)
    print(f"{DIM}  Hasta: {pseudonym}  Seans: {session_date}{RESET}")

    transcript = transcribe(audio_path, model_size)

    # Çıktı: pseudonym ile (KVKK)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = pseudonym.replace("#", "")
    out_path = REPORTS_DIR / f"{stamp}_{safe_name}_{session_date}.transcript.txt"
    out_path.write_text(transcript, encoding="utf-8")
    print(f"{GREEN}  ✓ Transkript: {out_path.name}{RESET}")

    # Audit log: ses dosyası işlendi (KVKK m.12)
    try:
        from state_store import get_default_store
        import json as _json
        with get_default_store()._cursor() as cur:
            cur.execute(
                "INSERT INTO processed (namespace, key, meta) VALUES (?, ?, ?)",
                (
                    "transcription",
                    f"{pseudonym}_{session_date}",
                    _json.dumps({
                        "patient_uuid": patient_uuid,
                        "patient_pseudonym": pseudonym,
                        "session_date": session_date,
                        "audio_filename_redacted": audio_path.name[:30] + "...",
                        "transcript_length": len(transcript),
                        "transcript_path": str(out_path),
                        "model": model_size,
                        "processed_at": datetime.now().astimezone().isoformat(),
                    }),
                ),
            )
    except Exception as exc:
        print(f"{YELLOW}  ! Audit log atlandı: {exc}{RESET}")

    # İşlenen ses dosyasını audio_processed/'a taşı (opsiyonel)
    if archive:
        new_audio_path = PROCESSED_DIR / audio_path.name
        try:
            audio_path.rename(new_audio_path)
            print(f"{DIM}  ↪ Ses dosyası taşındı: audio_processed/{RESET}")
        except Exception:
            pass  # taşıma başarısız → orjinal kalır

    return {"transcript_path": str(out_path), "pseudonym": pseudonym}


def main() -> int:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument("audio", nargs="?", help="Ses dosyası yolu")
    parser.add_argument("--batch", action="store_true",
                        help="audio_inbox/ klasöründeki tüm dosyalar")
    parser.add_argument(
        "--model", default="medium",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        help="Whisper modeli (default: medium)",
    )
    parser.add_argument(
        "--archive", action="store_true",
        help="İşlenen ses dosyasını audio_processed/ klasörüne taşı",
    )
    args = parser.parse_args()

    print(f"{YELLOW}Transcribe Only{RESET}")
    print(f"{DIM}Model: {args.model} | KVKK: pseudonym ile çıktı{RESET}\n")

    audio_files: list[Path] = []
    if args.batch:
        inbox = Path(__file__).resolve().parent.parent / "audio_inbox"
        for ext in ("*.m4a", "*.mp3", "*.wav", "*.opus", "*.ogg", "*.flac"):
            audio_files.extend(inbox.glob(ext))
        if not audio_files:
            print(f"{YELLOW}audio_inbox/ klasöründe ses dosyası yok.{RESET}")
            return 0
    elif args.audio:
        audio_files = [Path(args.audio)]
    else:
        parser.error("Ses dosyası veya --batch zorunlu")

    print(f"{GREEN}{len(audio_files)} dosya işlenecek{RESET}\n")

    summary = {"success": 0, "failed": 0}
    for audio_path in audio_files:
        if not audio_path.exists():
            print(f"{RED}✗ Bulunamadı: {audio_path}{RESET}")
            summary["failed"] += 1
            continue
        try:
            process_audio(audio_path, args.model, archive=args.archive)
            summary["success"] += 1
        except Exception as exc:
            summary["failed"] += 1
            print(f"{RED}✗ Hata: {exc}{RESET}")
            import traceback
            traceback.print_exc()
        print()

    print(f"{YELLOW}━━━ Özet ━━━{RESET}")
    print(f"  Başarılı: {summary['success']}")
    print(f"  Başarısız: {summary['failed']}")
    print(f"  Çıktı: {REPORTS_DIR}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
