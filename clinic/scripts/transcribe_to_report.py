#!/usr/bin/env python3
"""
Hızlı Ses → Rapor Pipeline (KVKK uyumlu, lokal)

Çocuk-Ergen Psikiyatrisi seans kayıtlarını klinik rapora dönüştürür.
Tüm işlem yerel makinada — ses dosyası hiçbir cloud servise gitmez.

Pipeline:
1. Faster-Whisper (lokal model) → ses → Türkçe transkript
2. Ollama (lokal LLM) → transkript → 3 format çıktı:
   - SOAP klinik notu (.txt)
   - Serbest metin rapor (.txt)
   - Yapılandırılmış JSON (.json) — risk + DSM-5/ICD-10 önerisi

Kullanım:
    # Tek dosya
    python scripts/transcribe_to_report.py audio_inbox/seans1.m4a

    # Klasör — audio_inbox/ tüm dosyalar
    python scripts/transcribe_to_report.py --batch

    # Format seçimi (default: tüm üçü)
    python scripts/transcribe_to_report.py --format soap audio_inbox/seans1.m4a
    python scripts/transcribe_to_report.py --format report audio_inbox/seans1.m4a
    python scripts/transcribe_to_report.py --format json audio_inbox/seans1.m4a

KVKK: Ses dosyası, transkript, rapor — hepsi yerel diskte (BitLocker).
Notion'a manuel olarak siz karar verirsiniz (pseudonym + KLINIK kısım).
"""

import argparse
import json
import os
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

import requests

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
# medium: Türkçe için iyi denge (3x hızlı, doğruluk %95+)
# large-v3: en doğru ama 3x yavaş, GPU önerilir
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "medium")
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)


def transcribe_audio(audio_path: Path) -> str:
    """Faster-Whisper ile lokal transkripsiyon."""
    from faster_whisper import WhisperModel

    print(f"{DIM}  Whisper modelini yüklüyor ({WHISPER_MODEL_SIZE})...{RESET}")
    # CPU varsayılan; GPU varsa device='cuda' compute_type='float16'
    model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")

    print(f"{DIM}  Transkripsiyon başladı...{RESET}")
    t0 = time.time()
    segments, info = model.transcribe(
        str(audio_path),
        language="tr",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    text_parts = []
    for seg in segments:
        text_parts.append(seg.text.strip())
    transcript = " ".join(text_parts)
    elapsed = time.time() - t0
    duration = getattr(info, "duration", 0)
    print(
        f"{GREEN}  ✓ Transkript hazır ({len(transcript)} karakter, "
        f"ses: {duration:.1f}s, işlem: {elapsed:.1f}s){RESET}"
    )
    return transcript


def call_ollama(prompt: str, model: str = OLLAMA_MODEL) -> str:
    """Ollama REST ile prompt çalıştır, yanıt döner."""
    r = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.3}},
        timeout=600,  # 20B model yavaş olabilir
    )
    r.raise_for_status()
    return r.json().get("response", "").strip()


SOAP_PROMPT = """Sen deneyimli bir Çocuk ve Ergen Psikiyatrisi uzmanısın.
Aşağıdaki seans transkriptini SOAP formatında klinik nota dönüştür.
Yanıtın TAM SADECE aşağıdaki başlıklar altında olsun, başka açıklama yapma.

Format:

S — SUBJECTIVE (Hasta/Aile Beyanları)
[Hastanın ve ailenin kendi ifadeleri, şikayetler, hikaye]

O — OBJECTIVE (Gözlemler)
[Mental durum muayenesi, davranış gözlemleri, etkileşim]

A — ASSESSMENT (Değerlendirme)
[Ön tanı düşünceleri, diferansiyel tanı]

P — PLAN (Plan)
[İlaç, terapi, kontrol, ek değerlendirme planları]

Türkçe yaz, kısa ve net ol. Spekülasyon yapma — transkriptte olmayan
bilgi UYDURMA. Belirsiz noktaları "[netleştirilmeli]" olarak işaretle.

═══════════════════════════════════════════════════════════════
TRANSKRIPT:
{transcript}
═══════════════════════════════════════════════════════════════
"""

REPORT_PROMPT = """Sen deneyimli bir Çocuk ve Ergen Psikiyatrisi uzmanısın.
Aşağıdaki seans transkriptini akıcı bir klinik rapor metnine dönüştür.
3-5 paragraf, profesyonel dil, mail veya hasta dosyası için uygun.

Yapı:
- 1. paragraf: Başvuru nedeni + hasta profili (yaş, sınıf, vs.)
- 2-3. paragraf: Ana şikayetler + öykü + gözlemler + akıl yürütmen
- Son paragraf: Plan + öneriler + kontrol

Türkçe yaz. Spekülasyon yok — transkriptte yoksa ekleme. Belirsiz
noktalar için "[netleştirilmeli]" ekle.

═══════════════════════════════════════════════════════════════
TRANSKRIPT:
{transcript}
═══════════════════════════════════════════════════════════════
"""

CLINICAL_PROMPT = """Sen deneyimli bir Çocuk ve Ergen Psikiyatrisi uzmanısın.
Aşağıdaki seans transkriptini KAPSAMLI YAPILANDIRILMIŞ KLİNİK RAPOR
formatına dönüştür. Yanıtın TAM SADECE aşağıdaki başlıklar altında
olsun (Markdown başlıkları kullan). Spekülasyon yapma — transkriptte
olmayan bilgi UYDURMA. Belirsiz noktaları "[netleştirilmeli]" olarak
işaretle. Türkçe yaz.

═══════════════════════════════════════════════════════════════
ÇIKTI FORMATI (bu başlıkları kullan):
═══════════════════════════════════════════════════════════════

# KLİNİK GÖRÜŞME RAPORU

## 1. HASTA BİLGİLERİ
- Yaş: [yaş veya "belirtilmedi"]
- Sınıf/Eğitim: [sınıf veya "belirtilmedi"]
- Cinsiyet: [transkriptten anlaşılırsa]
- Eşlik eden: [aile, anne, baba, yalnız, vs.]

## 2. BAŞVURU NEDENİ
[Tek paragrafta ana şikayet]

## 3. ŞİMDİKİ HASTALIK ÖYKÜSÜ
[Şikayetlerin başlangıcı, seyri, şiddeti, tetikleyici faktörler. 2-3 paragraf]

## 4. SEMPTOMLAR
- [Semptom 1]
- [Semptom 2]
- [...]

## 5. MENTAL DURUM MUAYENESİ
- Görünüm/davranış: [seans sırasında gözlem]
- Konuşma: [hız, ton, içerik]
- Duygudurum/affekt: [varsa]
- Düşünce içeriği: [varsa]
- Bilişsel: [dikkat, hafıza, oryantasyon]
- İçgörü/yargı: [varsa]

## 6. AYIRICI TANI (DSM-5 / ICD-10)
| Tanı | ICD-10 | Olasılık | Destekleyen Bulgular |
|------|--------|----------|----------------------|
| [tanı 1] | [F90.0] | yüksek/orta/düşük | [bulgu özeti] |
| [tanı 2] | [Fxx.x] | yüksek/orta/düşük | [bulgu özeti] |

## 7. RİSK DEĞERLENDİRMESİ
- İntihar riski: [yok/düşük/orta/yüksek] — [kanıt]
- Kendine zarar verme: [yok/düşük/orta/yüksek] — [kanıt]
- Başkalarına zarar: [yok/düşük/orta/yüksek] — [kanıt]
- İhmal/istismar belirtileri: [yok/şüphe/güçlü] — [kanıt]
- Acil müdahale gerekli mi: [evet/hayır] — [neden]

## 8. TEDAVİ PLANI
### İlaç
[Öneri veya "şimdilik gereksiz" — gerekçe ile]

### Psikoterapi
- Tip: [BDT, oyun terapisi, aile terapisi, vs.]
- Sıklık: [haftada 1, 2 haftada 1, vs.]
- Hedef: [kısa vadeli ve uzun vadeli]

### Aile Yönlendirmesi
[Aileye verilecek öneriler]

### Ek Değerlendirme
- [Psikolojik test gerekli mi?]
- [Pediatri/nöroloji konsültasyonu?]
- [Eğitim/RAM yönlendirmesi?]

### Kontrol
- Sıradaki randevu: [X hafta sonra / aciliyetine göre]

## 9. KLİNİK NOTLAR
[Doktorun dikkat etmesi gereken noktalar, [netleştirilmeli] alanlar,
bir sonraki seansta sorulacak sorular]

═══════════════════════════════════════════════════════════════
TRANSKRIPT:
{transcript}
═══════════════════════════════════════════════════════════════
"""

JSON_PROMPT = """Sen deneyimli bir Çocuk ve Ergen Psikiyatrisi uzmanısın.
Aşağıdaki seans transkriptini analiz et ve TAM SADECE geçerli JSON çıktı ver.
Başka açıklama, markdown, yorum YOK — direkt JSON.

JSON şeması:
{{
  "patient_age": "yaş veya null",
  "patient_grade": "sınıf veya null",
  "chief_complaint": "ana şikayet, kısa cümle",
  "symptoms": ["semptom1", "semptom2", ...],
  "duration": "şikayet süresi veya null",
  "differential_diagnosis": [
    {{"icd10": "F90.0", "name": "DEHB - dikkatsizlik", "probability": "yüksek/orta/düşük"}}
  ],
  "risk_assessment": {{
    "suicide_risk": "yok/düşük/orta/yüksek",
    "harm_risk": "yok/düşük/orta/yüksek",
    "abuse_indicators": "yok/şüphe/güçlü"
  }},
  "treatment_plan": {{
    "medication": "öneri veya null",
    "therapy": "tip ve sıklık",
    "follow_up_weeks": 2
  }},
  "notes_for_doctor": "klinik gözlem, [netleştirilmeli] alanları, ek dikkat noktaları"
}}

Spekülasyon yok. Transkriptte yoksa null veya boş array kullan.

═══════════════════════════════════════════════════════════════
TRANSKRIPT:
{transcript}
═══════════════════════════════════════════════════════════════
"""


def _extract_name_and_date(audio_path: Path) -> tuple[str, str]:
    """
    Dosya adından gerçek isim + tarihi parse et.
    Format örneği: 'şimal duru özsoy 03.04.2026.m4a'
    Heuristik: tarih = DD.MM.YYYY pattern; isim = tarihten öncesi
    """
    import re
    stem = audio_path.stem
    # Tarih pattern (DD.MM.YYYY veya DD-MM-YYYY veya DD_MM_YYYY)
    date_match = re.search(r"(\d{1,2})[._-](\d{1,2})[._-](\d{4})", stem)
    if date_match:
        date_iso = f"{date_match.group(3)}-{int(date_match.group(2)):02d}-{int(date_match.group(1)):02d}"
        name_part = stem[:date_match.start()].strip(" -_")
    else:
        date_iso = datetime.now().strftime("%Y-%m-%d")
        name_part = stem.strip(" -_")
    return (name_part or "unknown_patient"), date_iso


def process_audio(audio_path: Path, formats: list[str]) -> dict:
    """
    Tek ses dosyası için pipeline.

    KVKK güvencesi:
    - Dosya adındaki gerçek isim çıkartılıp patient_registry'ye Fernet ile
      şifreli yazılır (yerel)
    - Çıktı dosyaları PSEUDONYM (#abcd-1234) ile adlandırılır
    - Gerçek isim asla çıktı dosya adında, transkript'te veya rapor'da
      görünmez (transcript ortasında olabilir → uzman manuel maskeleyecek)
    """
    print(f"{YELLOW}► {audio_path.name}{RESET}")

    # 1. İsim + tarih parse + pseudonym al
    real_name, session_date = _extract_name_and_date(audio_path)
    from patient_registry import get_default_registry
    from pii_crypto import short_pseudonym

    registry = get_default_registry()
    existing = registry.find_by_name(real_name)
    if existing:
        patient_uuid = existing[0]["uuid"]
    else:
        patient_uuid = registry.create_patient(full_name=real_name)
    pseudonym = short_pseudonym(patient_uuid)
    print(f"{DIM}  Hasta: {pseudonym} (gerçek isim Fernet ile yerel'de){RESET}")
    print(f"{DIM}  Seans tarihi: {session_date}{RESET}")

    transcript = transcribe_audio(audio_path)

    # 2. Çıktıyı PSEUDONYM ile adlandır (KVKK güvencesi)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = pseudonym.replace("#", "")  # dosya isminde # bazı işletim sistemlerinde sorunlu
    out_base = REPORTS_DIR / f"{stamp}_{safe_name}_{session_date}"
    transcript_path = out_base.with_suffix(".transcript.txt")
    transcript_path.write_text(transcript, encoding="utf-8")
    print(f"{GREEN}  ✓ Transkript: {transcript_path.name}{RESET}")

    results = {"transcript_path": str(transcript_path)}

    if "clinical" in formats:
        print(f"{DIM}  Yapılandırılmış klinik rapor üretiliyor (Ollama {OLLAMA_MODEL})...{RESET}")
        t0 = time.time()
        clinical = call_ollama(CLINICAL_PROMPT.format(transcript=transcript))
        clinical_path = out_base.with_suffix(".clinical.md")
        clinical_path.write_text(clinical, encoding="utf-8")
        print(
            f"{GREEN}  ✓ Klinik rapor: {clinical_path.name} ({time.time()-t0:.1f}s){RESET}"
        )
        results["clinical_path"] = str(clinical_path)

    if "soap" in formats:
        print(f"{DIM}  SOAP üretiliyor (Ollama {OLLAMA_MODEL})...{RESET}")
        t0 = time.time()
        soap = call_ollama(SOAP_PROMPT.format(transcript=transcript))
        soap_path = out_base.with_suffix(".soap.txt")
        soap_path.write_text(soap, encoding="utf-8")
        print(
            f"{GREEN}  ✓ SOAP: {soap_path.name} ({time.time()-t0:.1f}s){RESET}"
        )
        results["soap_path"] = str(soap_path)

    if "report" in formats:
        print(f"{DIM}  Rapor üretiliyor...{RESET}")
        t0 = time.time()
        report = call_ollama(REPORT_PROMPT.format(transcript=transcript))
        report_path = out_base.with_suffix(".report.txt")
        report_path.write_text(report, encoding="utf-8")
        print(
            f"{GREEN}  ✓ Rapor: {report_path.name} ({time.time()-t0:.1f}s){RESET}"
        )
        results["report_path"] = str(report_path)

    if "json" in formats:
        print(f"{DIM}  Yapılandırılmış JSON üretiliyor...{RESET}")
        t0 = time.time()
        raw = call_ollama(JSON_PROMPT.format(transcript=transcript))
        # JSON parse — markdown'a sarılı olabilir, temizle
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.rsplit("```", 1)[0].strip()
        try:
            parsed = json.loads(cleaned)
            json_path = out_base.with_suffix(".clinical.json")
            json_path.write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(
                f"{GREEN}  ✓ JSON: {json_path.name} ({time.time()-t0:.1f}s){RESET}"
            )
            results["json_path"] = str(json_path)
        except json.JSONDecodeError as exc:
            # Parse hatası — raw çıktıyı kaydet
            err_path = out_base.with_suffix(".json_raw.txt")
            err_path.write_text(raw, encoding="utf-8")
            print(
                f"{YELLOW}  ! JSON parse hatası, raw kaydedildi: {err_path.name}{RESET}"
            )
            print(f"{DIM}    Hata: {exc}{RESET}")
            results["json_raw_path"] = str(err_path)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument("audio", nargs="?", help="Ses dosyası yolu")
    parser.add_argument(
        "--batch", action="store_true",
        help="audio_inbox/ klasöründeki tüm dosyaları işle",
    )
    parser.add_argument(
        "--format", choices=["clinical", "soap", "report", "json", "all"],
        default="clinical",
        help="Çıktı formatı (default: clinical = (C) yapılandırılmış klinik rapor + JSON)",
    )
    args = parser.parse_args()

    if args.format == "all":
        formats = ["clinical", "soap", "report", "json"]
    elif args.format == "clinical":
        # (C) seçimi: insan-okunabilir markdown rapor + yapılandırılmış JSON
        formats = ["clinical", "json"]
    else:
        formats = [args.format]

    print(f"{YELLOW}Ses → Rapor Pipeline{RESET}")
    print(f"{DIM}Whisper: {WHISPER_MODEL_SIZE} | Ollama: {OLLAMA_MODEL} | Format: {formats}{RESET}")
    print()

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
        parser.error("audio veya --batch zorunlu")

    print(f"{GREEN}{len(audio_files)} ses dosyası işlenecek{RESET}\n")

    summary = {"success": 0, "failed": 0}
    for audio_path in audio_files:
        if not audio_path.exists():
            print(f"{RED}✗ Bulunamadı: {audio_path}{RESET}")
            summary["failed"] += 1
            continue
        try:
            process_audio(audio_path, formats)
            summary["success"] += 1
        except Exception as exc:
            summary["failed"] += 1
            print(f"{RED}✗ Hata: {exc}{RESET}")
        print()

    print(f"{YELLOW}━━━ Özet ━━━{RESET}")
    print(f"  başarılı: {summary['success']}")
    print(f"  başarısız: {summary['failed']}")
    print(f"  çıktı klasörü: {REPORTS_DIR}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
