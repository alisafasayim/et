#!/usr/bin/env python3
"""
Transcript → Klinik Rapor (chunked synthesis pipeline)

Whisper'in ürettiği uzun transkripti chunked olarak işler:
1. Transkripti 3000 token'lık parçalara böl
2. Her parça için "ana noktalar" özeti çıkar (kısa, hızlı)
3. Tüm özetleri birleştir → final yapılandırılmış klinik rapor

Bu yaklaşım "lost-in-the-middle" problemini çözer + büyük modellerin
context limitini aşmaz + her chunk hızlı (1-2 dk).

Kullanım:
    # En son transkripti işle (default)
    python scripts/transcript_to_clinical.py

    # Belirli transcript dosyası
    python scripts/transcript_to_clinical.py reports/20260504_051831_*.transcript.txt

    # Daha hızlı LLM (kalite biraz düşer)
    OLLAMA_MODEL=llama3.1:latest python scripts/transcript_to_clinical.py
"""

import argparse
import json
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

import requests

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
DIM = "\033[2m"
RESET = "\033[0m"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

CHUNK_SIZE_CHARS = 8000  # ~2700 token, gpt-oss:20b'de hızlı + 8K context'in altı
TIMEOUT_SEC = 1800  # 30 dk per chunk (CPU'da 20b yavaş olabilir)


def call_ollama_streaming(prompt: str, model: str = OLLAMA_MODEL) -> str:
    """Streaming ile Ollama çağrı — uzun yanıtlarda timeout sorunu olmaz."""
    response = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {"temperature": 0.3, "num_ctx": 8192},
        },
        timeout=TIMEOUT_SEC,
        stream=True,
    )
    response.raise_for_status()

    full_text = []
    for line in response.iter_lines():
        if not line:
            continue
        try:
            chunk = json.loads(line)
            if "response" in chunk:
                full_text.append(chunk["response"])
            if chunk.get("done"):
                break
        except json.JSONDecodeError:
            continue

    return "".join(full_text).strip()


def chunk_transcript(text: str, max_chars: int = CHUNK_SIZE_CHARS) -> list[str]:
    """Transkripti cümle sınırlarında parçala (token-aware değil ama yakın)."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = []
    current_len = 0

    for sent in sentences:
        if current_len + len(sent) > max_chars and current:
            chunks.append(" ".join(current))
            current = [sent]
            current_len = len(sent)
        else:
            current.append(sent)
            current_len += len(sent) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks


CHUNK_SUMMARY_PROMPT = """Aşağıdaki seans transkripti parçası ({chunk_num}/{total_chunks})
için, çocuk-ergen psikiyatrisi açısından önemli noktaları çıkar:

- Hasta/aile beyanları (semptom, şikayet, hikaye)
- Doktor gözlemleri / sorular
- Risk işaretleri (intihar, kendine zarar, istismar şüphesi)
- İlaç / tedavi konusu
- Ödevler, planlar

Madde işaretli liste, kısa ve net. Spekülasyon yapma, transkriptte
olmayan bilgiyi UYDURMA. Türkçe yaz.

═══════════════════════════════════════════════════════════════
TRANSKRIPT PARÇASI ({chunk_num}/{total_chunks}):
{chunk_text}
═══════════════════════════════════════════════════════════════

ÖNEMLI NOKTALAR:
"""


SYNTHESIS_PROMPT = """Sen deneyimli bir Çocuk ve Ergen Psikiyatrisi uzmanısın.
Aşağıda bir seansın {total_chunks} parçaya bölünmüş özet noktaları var.
Bu özetleri birleştirip KAPSAMLI YAPILANDIRILMIŞ KLİNİK RAPOR oluştur.
Türkçe yaz. Spekülasyon yapma — özetlerde olmayan bilgi uydurma.
Belirsiz noktaları "[netleştirilmeli]" işaretle.

═══════════════════════════════════════════════════════════════
ÇIKTI FORMATI (Markdown başlıkları kullan):
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
[Psikolojik test, pediatri/nöroloji konsültasyonu, RAM yönlendirmesi]

### Kontrol
- Sıradaki randevu: [X hafta sonra / aciliyetine göre]

## 9. KLİNİK NOTLAR
[Doktorun dikkat etmesi gereken noktalar, [netleştirilmeli] alanlar,
bir sonraki seansta sorulacak sorular]

═══════════════════════════════════════════════════════════════
SEANS ÖZETLERİ:
{combined_summaries}
═══════════════════════════════════════════════════════════════
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "transcript", nargs="?",
        help="Transcript dosyası yolu (verilmezse en son transcript kullanılır)",
    )
    args = parser.parse_args()

    print(f"{YELLOW}Transcript → Klinik Rapor{RESET}")
    print(f"{DIM}Model: {OLLAMA_MODEL} | Chunk size: {CHUNK_SIZE_CHARS} char{RESET}")
    print()

    # Transcript dosyası belirle
    if args.transcript:
        transcript_path = Path(args.transcript)
    else:
        transcripts = sorted(REPORTS_DIR.glob("*.transcript.txt"))
        if not transcripts:
            print(f"{RED}✗ {REPORTS_DIR}/ klasöründe transcript yok{RESET}")
            return 1
        transcript_path = transcripts[-1]
        print(f"{DIM}En son transcript: {transcript_path.name}{RESET}")

    text = transcript_path.read_text(encoding="utf-8")
    print(f"{GREEN}✓{RESET} Transcript yüklendi: {len(text)} karakter (~{len(text)//3} token)")

    # Chunking
    chunks = chunk_transcript(text)
    print(f"{GREEN}✓{RESET} {len(chunks)} parçaya bölündü")
    print()

    # Her chunk için özet
    summaries = []
    for i, chunk in enumerate(chunks, 1):
        print(f"{YELLOW}► Chunk {i}/{len(chunks)} özetleniyor ({len(chunk)} char)...{RESET}")
        t0 = time.time()
        try:
            summary = call_ollama_streaming(
                CHUNK_SUMMARY_PROMPT.format(
                    chunk_num=i,
                    total_chunks=len(chunks),
                    chunk_text=chunk,
                )
            )
            summaries.append(f"=== PARÇA {i}/{len(chunks)} ===\n{summary}")
            print(f"{GREEN}  ✓ Özet hazır ({len(summary)} char, {time.time()-t0:.1f}s){RESET}")
        except Exception as exc:
            print(f"{RED}  ✗ Hata: {exc}{RESET}")
            return 1

    # Synthesis: tüm özetler → final klinik rapor
    print()
    print(f"{YELLOW}► Final klinik rapor sentezleniyor...{RESET}")
    t0 = time.time()
    combined = "\n\n".join(summaries)
    clinical = call_ollama_streaming(
        SYNTHESIS_PROMPT.format(
            total_chunks=len(chunks),
            combined_summaries=combined,
        )
    )
    print(f"{GREEN}  ✓ Klinik rapor hazır ({len(clinical)} char, {time.time()-t0:.1f}s){RESET}")

    # Çıktı: clinical.md
    out_path = transcript_path.with_suffix("").with_suffix(".clinical.md")
    # ".transcript.txt" → ".clinical.md" düzeltme
    base = transcript_path.name.replace(".transcript.txt", "")
    out_path = REPORTS_DIR / f"{base}.clinical.md"
    out_path.write_text(clinical, encoding="utf-8")
    print(f"{GREEN}✓ Klinik rapor: {out_path.name}{RESET}")

    # Özetler de kaydet (debug + transparency)
    summaries_path = REPORTS_DIR / f"{base}.summaries.md"
    summaries_path.write_text(combined, encoding="utf-8")
    print(f"{DIM}  Özetler: {summaries_path.name}{RESET}")

    print()
    print(f"{YELLOW}━━━ Tamamlandı ━━━{RESET}")
    print(f"  Toplam chunk: {len(chunks)}")
    print(f"  Klinik rapor: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
