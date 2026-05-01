"""
Modül 1: Akıllı Transkripsiyon ve Hasta Eşleştirme Motoru

Kurulum:
    pip install tinytag faster-whisper pyannote.audio ollama \
                google-api-python-client google-auth-httplib2 \
                google-auth-oauthlib

PyAnnote için HuggingFace token gereklidir:
    export HF_TOKEN="hf_..."
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

import ollama
from faster_whisper import WhisperModel
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from pyannote.audio import Pipeline
from tinytag import TinyTag

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

AUDIO_INBOX_DIR = Path(os.getenv("AUDIO_INBOX_DIR", "./audio_inbox"))
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
HF_TOKEN = os.getenv("HF_TOKEN", "")
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "large-v3")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# Klinik yerel saat dilimi. Tüm randevu/kayıt karşılaştırmaları
# bu zone'a hizalanır. Önceden naive datetime'lar UTC sayılıyordu
# → Türkiye'deki bir randevu 3 saat kayıyordu.
CLINIC_TZ = ZoneInfo(os.getenv("CLINIC_TZ", "Europe/Istanbul"))

# Randevu eşleştirmesi için tolerans (dakika)
APPOINTMENT_MATCH_WINDOW_MINUTES = 30

# ---------------------------------------------------------------------------
# 1. Metadata Çekimi
# ---------------------------------------------------------------------------

def get_audio_metadata(file_path: Path) -> dict:
    """tinytag ile ses dosyasından tarih/saat ve temel metadata okur."""
    tag = TinyTag.get(str(file_path))
    stat = file_path.stat()

    # mtime POSIX timestamp (UTC referanslı). Doğru çevrim için aware
    # UTC datetime, sonra klinik yerel zone'una çevir — eşleştirmede
    # randevular yerel TZ ile karşılaştırılır.
    recorded_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).astimezone(
        CLINIC_TZ
    )

    return {
        "file_path": str(file_path),
        "file_name": file_path.name,
        "duration_seconds": tag.duration,
        "recorded_at": recorded_at.isoformat(),
        "recorded_at_dt": recorded_at,
    }


# ---------------------------------------------------------------------------
# 2. Google Calendar Bağlantısı ve Randevu Çekimi
# ---------------------------------------------------------------------------

def get_calendar_service():
    """OAuth2 akışıyla Google Calendar API servisini döner."""
    creds = None
    if Path(GOOGLE_TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, CALENDAR_SCOPES)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            GOOGLE_CREDENTIALS_FILE, CALENDAR_SCOPES
        )
        creds = flow.run_local_server(port=0)
        Path(GOOGLE_TOKEN_FILE).write_text(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def _parse_calendar_dt(value: str) -> datetime:
    """
    Google Calendar event start/end değerini aware datetime'a çevirir.
    All-day randevularda 'date' (YYYY-MM-DD) gelir, normalde dateTime
    ISO with offset. Naive değerler klinik yerel TZ kabul edilir.
    """
    # All-day event: sadece YYYY-MM-DD
    if "T" not in value:
        d = datetime.fromisoformat(value)
        return d.replace(hour=0, tzinfo=CLINIC_TZ)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CLINIC_TZ)
    return dt


def fetch_day_appointments(service, target_date: datetime) -> list[dict]:
    """Verilen güne (klinik yerel TZ'sinde) ait tüm randevuları çeker."""
    # target_date naive ise yerel TZ kabul et
    if target_date.tzinfo is None:
        target_date = target_date.replace(tzinfo=CLINIC_TZ)
    local = target_date.astimezone(CLINIC_TZ)
    day_start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_local = day_start_local + timedelta(days=1)

    events_result = (
        service.events()
        .list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=day_start_local.astimezone(timezone.utc).isoformat(),
            timeMax=day_end_local.astimezone(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    appointments = []
    for event in events_result.get("items", []):
        start_str = event["start"].get("dateTime", event["start"].get("date"))
        end_str = event["end"].get("dateTime", event["end"].get("date"))
        appointments.append(
            {
                "event_id": event["id"],
                "summary": event.get("summary", ""),
                "description": event.get("description", ""),
                "start": start_str,
                "end": end_str,
                "start_dt": _parse_calendar_dt(start_str),
            }
        )
    return appointments


def match_appointments(recorded_at: datetime, appointments: list[dict]) -> list[dict]:
    """
    Ses kaydının saatine yakın (±APPOINTMENT_MATCH_WINDOW_MINUTES) randevuları döner.
    Tüm karşılaştırmalar aware datetime üzerinden yapılır; recorded_at
    naive gelirse klinik yerel TZ kabul edilir.

    Bu basit time-window eşleştirmesi — çoklu hasta segmentasyonu,
    parçalı kayıt birleştirmesi gibi gelişmiş senaryolar için
    smart_matcher.SmartMatcher.match_audio() kullanın (full transkript
    + üç-kaynak çapraz referans gerektirir).
    """
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=CLINIC_TZ)

    window = timedelta(minutes=APPOINTMENT_MATCH_WINDOW_MINUTES)
    matched = []
    for appt in appointments:
        appt_start = appt["start_dt"]
        if appt_start.tzinfo is None:
            appt_start = appt_start.replace(tzinfo=CLINIC_TZ)
        if abs(recorded_at - appt_start) <= window:
            matched.append(appt)
    return matched


def smart_match_audio_to_patients(
    file_path: Path,
    transcription_segments: list[dict],
    full_transcript_text: str,
    appointments: list[dict],
    recorded_at: datetime,
):
    """
    Smart matcher köprüsü: Modül 1'in dict-tabanlı çıktılarını
    smart_matcher'in dataclass arayüzüne adapte eder, sonra
    match_audio() çağırır.

    Geriye smart_matcher.MatchResult döner. Çoklu hasta + parçalı
    kayıt + isimsiz dosya senaryolarında detect_session_segments()
    LLM çağrısına alternatif olarak kullanılabilir.

    Şu an opsiyonel — process_audio_file LLM-tabanlı detect'i
    çağırıyor. SMART_MATCHER_ENABLED=true ile aktive edilebilir.
    """
    from clinic_helpers import (
        Appointment,
        TranscriptionResult,
        TranscriptionSegment,
    )
    from smart_matcher import SmartMatcher

    appts_dc = [
        Appointment(
            patient_name=a.get("summary", ""),
            start_time=a["start_dt"],
            end_time=a.get(
                "end_dt", a["start_dt"] + timedelta(minutes=45)
            ),
            summary=a.get("summary", ""),
            event_id=a.get("event_id", ""),
        )
        for a in appointments
    ]
    transcription_dc = TranscriptionResult(
        audio_path=str(file_path),
        full_text=full_transcript_text,
        segments=[TranscriptionSegment.from_dict(s) for s in transcription_segments],
        duration_seconds=transcription_segments[-1]["end"]
        if transcription_segments
        else 0.0,
        language="tr",
    )
    audio_metadata = {
        "file_path": str(file_path),
        "recorded_at": recorded_at,
        "duration": transcription_dc.duration_seconds,
    }
    matcher = SmartMatcher()
    return matcher.match_audio(transcription_dc, appts_dc, audio_metadata)


# ---------------------------------------------------------------------------
# 3. Transkripsiyon (faster-whisper) ve Diarization (pyannote)
# ---------------------------------------------------------------------------

# Modeller proses ömrü boyunca tek sefer yüklenir; her ses dosyasında
# yeniden initialize etmek (faster-whisper large-v3 ~3 GB, pyannote ~500 MB)
# I/O ve VRAM patlamasına yol açıyordu.
_whisper_model: WhisperModel | None = None
_diarization_pipeline: "Pipeline | None" = None


def _get_whisper_model() -> WhisperModel:
    global _whisper_model
    if _whisper_model is None:
        print(f"[Whisper] Model yükleniyor: {WHISPER_MODEL_SIZE} (ilk seferinde yavaş olabilir)")
        _whisper_model = WhisperModel(
            WHISPER_MODEL_SIZE, device="auto", compute_type="auto"
        )
    return _whisper_model


def _get_diarization_pipeline() -> Pipeline:
    global _diarization_pipeline
    if _diarization_pipeline is None:
        if not HF_TOKEN:
            raise EnvironmentError(
                "HF_TOKEN ayarlanmamış; pyannote/speaker-diarization-3.1 indirilemez."
            )
        print("[PyAnnote] Diarization pipeline yükleniyor (ilk seferinde yavaş olabilir)")
        _diarization_pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=HF_TOKEN,
        )
    return _diarization_pipeline


def _maybe_preprocess(file_path: Path) -> Path:
    """
    AUDIO_PREPROCESS_ENABLED=true ise sesi WAV'a normalize edip kalite
    kontrolü yapar. pydub veya audio_preprocessing yüklü değilse veya
    feature kapalıysa orijinal dosya yolunu döner (no-op).

    Yüksek gürültülü m4a/mp3 dosyalarında transkripsiyon kalitesini
    artırır; sessiz başlangıç/bitiş kısımları kırpılır.
    """
    if os.getenv("AUDIO_PREPROCESS_ENABLED", "false").lower() not in (
        "1", "true", "yes", "on",
    ):
        return file_path
    try:
        from audio_preprocessing import AudioPreprocessor
    except ImportError:
        print("[Audio] audio_preprocessing modülü yüklü değil, atlanıyor")
        return file_path
    try:
        preprocessor = AudioPreprocessor()
        out = preprocessor.preprocess(str(file_path))
        return Path(out)
    except Exception as exc:
        # Pipeline başarısızsa orijinalle devam et — fail-soft (transkripsiyon
        # her halükârda denenmeli, ses kayıtları kıymetli).
        print(f"[Audio] Ön-işleme başarısız (orijinalle devam): {exc}")
        return file_path


def transcribe_audio(file_path: Path) -> list[dict]:
    """
    faster-whisper ile transkripsiyon yapar.
    Her segment için başlangıç/bitiş süresi ve metni döner.

    AUDIO_PREPROCESS_ENABLED=true iken önce normalize/gürültü-azalt
    pipeline'ından geçirilir (pydub bağımlı).
    """
    file_path = _maybe_preprocess(file_path)
    model = _get_whisper_model()
    segments, info = model.transcribe(str(file_path), beam_size=5, language="tr")

    print(f"[Whisper] Dil: {info.language}, Olasılık: {info.language_probability:.2f}")

    return [
        {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        }
        for seg in segments
    ]


def diarize_audio(file_path: Path) -> list[dict]:
    """
    pyannote.audio ile konuşmacı ayrımı (diarization) yapar.
    Her konuşma bloğu için başlangıç, bitiş ve konuşmacı etiketi döner.
    """
    pipeline = _get_diarization_pipeline()
    diarization = pipeline(str(file_path))

    turns = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turns.append(
            {
                "start": turn.start,
                "end": turn.end,
                "speaker": speaker,
            }
        )
    return turns


def merge_transcript_with_diarization(
    segments: list[dict], turns: list[dict]
) -> list[dict]:
    """
    Transkript segmentlerini diarization sonuçlarıyla birleştirir.
    Her metin segmentine en çok örtüşen konuşmacıyı atar.
    """
    merged = []
    for seg in segments:
        best_speaker = "UNKNOWN"
        best_overlap = 0.0

        seg_start, seg_end = seg["start"], seg["end"]
        for turn in turns:
            overlap_start = max(seg_start, turn["start"])
            overlap_end = min(seg_end, turn["end"])
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = turn["speaker"]

        merged.append(
            {
                "start": seg_start,
                "end": seg_end,
                "speaker": best_speaker,
                "text": seg["text"],
            }
        )
    return merged


def format_transcript_for_llm(merged_segments: list[dict]) -> str:
    """Birleştirilmiş transkripti LLM'e göndermek için düz metin formatına çevirir."""
    lines = []
    for seg in merged_segments:
        timestamp = f"[{seg['start']:.1f}s - {seg['end']:.1f}s]"
        lines.append(f"{seg['speaker']} {timestamp}: {seg['text']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. LLM ile Karmaşıklık Çözümü (Ollama)
# ---------------------------------------------------------------------------

SEGMENT_DETECTION_PROMPT = """Sen deneyimli bir çocuk psikiyatristi asistanısın.
Aşağıda bir ses kaydına ait transkript ve o güne ait takvim randevusu bilgileri var.

TAKVİM RANDEVULARI:
{appointments_json}

TRANSKRİPT:
{transcript}

Görevin:
1. Transkriptte birden fazla hasta görüşmesi geçiyorsa, her görüşmenin hangi zaman diliminde başlayıp bittiğini tespit et.
2. Her segmenti ilgili takvim randevusuyla eşleştir.
3. Eğer tüm transkript tek bir hastaya aitse, bunu da belirt.

Yanıtını SADECE aşağıdaki JSON formatında ver, başka hiçbir açıklama ekleme:
{{
  "segments": [
    {{
      "appointment_id": "<takvim event_id veya 'unknown'>",
      "patient_name": "<hasta adı, bilinemiyorsa 'unknown'>",
      "transcript_start_second": <sayı>,
      "transcript_end_second": <sayı>,
      "confidence": "<high|medium|low>"
    }}
  ]
}}"""


def detect_session_segments(
    transcript: str, appointments: list[dict]
) -> list[dict]:
    """
    Ollama üzerinden lokal LLM'e transkript ve randevu verisi göndererek
    kayıttaki hasta segmentlerini tespit eder.
    """
    appointments_clean = [
        {k: v for k, v in a.items() if k != "start_dt"} for a in appointments
    ]

    prompt = SEGMENT_DETECTION_PROMPT.format(
        appointments_json=json.dumps(appointments_clean, ensure_ascii=False, indent=2),
        transcript=transcript,
    )

    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.1},
    )

    raw = response["message"]["content"].strip()
    # JSON bloğunu ayıkla (LLM bazen markdown code fence ekleyebilir)
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        raise ValueError(f"LLM geçerli JSON döndürmedi:\n{raw}")

    return json.loads(json_match.group())["segments"]


# ---------------------------------------------------------------------------
# 5. SOAP Notu Üretimi (Ollama)
# ---------------------------------------------------------------------------

SOAP_PROMPT = """Sen deneyimli bir çocuk ve ergen psikiyatristi asistanısın.
Aşağıda bir hasta görüşmesine ait transkript var. Bu transkripti analiz ederek
Türkçe ve çocuk psikiyatrisine uygun SOAP formatında klinik not oluştur.

HASTA: {patient_name}
RANDEVU: {appointment_summary}

TRANSKRİPT:
{transcript}

KULLANILABİLİR DSM-5-TR TANI KODLARI (sık kullanılanlar):
{dsm5_codes_hint}

Tanı önerirken yukarıdaki kod listesinden en uygun olanları seç ve
"dsm5_suggested_codes" alanına en fazla 3 tane ekle (örn: "F90.0").
Liste dışında bir kod uydurma; uygun kod yoksa boş dizi bırak.

Yanıtını SADECE aşağıdaki JSON formatında ver:
{{
  "patient_name": "{patient_name}",
  "appointment_id": "{appointment_id}",
  "soap": {{
    "subjective": {{
      "chief_complaint": "<Ana şikayet>",
      "history_of_present_illness": "<Mevcut hastalık öyküsü>",
      "family_history": "<Aile öyküsü>",
      "developmental_history": "<Gelişimsel öykü>"
    }},
    "objective": {{
      "mental_status_exam": "<Ruhsal durum muayenesi>",
      "behavior_observations": "<Davranış gözlemleri>",
      "affect_mood": "<Duygudurum ve afekt>"
    }},
    "assessment": {{
      "provisional_diagnosis": "<Ön tanı (Türkçe)>",
      "differential_diagnosis": "<Ayırıcı tanı>",
      "risk_assessment": "<Risk değerlendirmesi>",
      "dsm5_suggested_codes": ["<F-kodu>", "..."]
    }},
    "plan": {{
      "medication": "<İlaç tedavisi (varsa)>",
      "therapy": "<Psikoterapi planı>",
      "parent_guidance": "<Aile/veli yönlendirmesi>",
      "follow_up": "<Takip planı>",
      "referrals": "<Yönlendirmeler (varsa)>"
    }}
  }},
  "generated_at": "{generated_at}"
}}"""


def _dsm5_codes_hint() -> str:
    """SOAP prompt'una eklenen DSM-5 kod listesini döner.

    Tüm 38 kodu basmak prompt token'ını şişirir; en sık karşılaşılan
    çocuk-ergen tanılarından kategori başlıkları ile özetlenmiş liste.
    """
    try:
        from dsm5_codes import DSM5_CODES
    except ImportError:
        return "(DSM-5 modülü yüklü değil)"

    by_cat: dict[str, list[str]] = {}
    for diag in DSM5_CODES.values():
        by_cat.setdefault(diag.category, []).append(f"{diag.code} = {diag.name_tr}")

    lines = []
    for cat, items in sorted(by_cat.items()):
        lines.append(f"- {cat}:")
        for item in items:
            lines.append(f"    {item}")
    return "\n".join(lines)


def generate_soap_note(
    transcript_segment: str,
    patient_name: str,
    appointment_id: str,
    appointment_summary: str,
    appointment_start: str = "",
) -> dict:
    """
    Ollama üzerinden lokal LLM'e transkript segmenti göndererek
    SOAP formatında klinik not üretir.

    appointment_start: Randevunun gerçek başlangıç ISO zamanı.
    LLM'in echo'suna güvenmek yerine bu değer post-process olarak
    SOAP JSON'una yazılır (Notion'da "Randevu Tarihi" sütunu için).
    """
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    prompt = SOAP_PROMPT.format(
        patient_name=patient_name,
        appointment_id=appointment_id,
        appointment_summary=appointment_summary,
        transcript=transcript_segment,
        generated_at=generated_at,
        dsm5_codes_hint=_dsm5_codes_hint(),
    )

    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.2},
    )

    raw = response["message"]["content"].strip()
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        raise ValueError(f"LLM geçerli JSON döndürmedi:\n{raw}")

    soap = json.loads(json_match.group())

    # Güvenilir metadata'yı LLM'in çıktısının üzerine yaz; LLM bazen
    # alanları boş/yanlış echo ediyor.
    soap["patient_name"] = patient_name
    soap["appointment_id"] = appointment_id
    soap["appointment_summary"] = appointment_summary
    soap["appointment_start"] = appointment_start
    soap["generated_at"] = generated_at

    return soap


# ---------------------------------------------------------------------------
# 6. Ana Orkestratör
# ---------------------------------------------------------------------------

def process_audio_file(file_path: Path, calendar_service) -> list[dict]:
    """
    Tek bir ses dosyasını uçtan uca işler:
    metadata → takvim eşleştirme → transkripsiyon → diarization →
    segment tespiti → SOAP üretimi
    """
    print(f"\n{'='*60}")
    print(f"İşleniyor: {file_path.name}")

    # 1. Metadata
    meta = get_audio_metadata(file_path)
    recorded_at = meta["recorded_at_dt"]
    print(f"Kayıt zamanı: {recorded_at.isoformat()}")

    # 2. Takvim eşleştirme
    appointments = fetch_day_appointments(calendar_service, recorded_at)
    matched = match_appointments(recorded_at, appointments)
    print(f"Eşleşen randevu sayısı: {len(matched)}")

    # 3. Transkripsiyon + Diarization
    print("Transkripsiyon başlıyor...")
    segments = transcribe_audio(file_path)
    print(f"  {len(segments)} transkript segmenti")

    print("Konuşmacı ayrımı başlıyor...")
    turns = diarize_audio(file_path)
    print(f"  {len(turns)} konuşmacı bloğu")

    merged = merge_transcript_with_diarization(segments, turns)
    full_transcript = format_transcript_for_llm(merged)

    # 4. Karmaşıklık çözümü
    print("LLM ile hasta segmentleri tespit ediliyor...")
    detected_segments = detect_session_segments(full_transcript, matched)
    print(f"  {len(detected_segments)} hasta segmenti bulundu")

    # 5. Her segment için SOAP notu
    soap_notes = []
    for seg in detected_segments:
        # İlgili transkript bloklarını doğrudan `merged` üzerinden seç.
        # Eski yaklaşım `full_transcript.splitlines()` ile zip'liyordu —
        # bir segmentin metni satır içeriyorsa zip kayıyor ve yanlış
        # hastaya satır atanıyordu.
        try:
            start_s = float(seg["transcript_start_second"])
            end_s = float(seg["transcript_end_second"])
        except (KeyError, TypeError, ValueError):
            start_s, end_s = 0.0, float("inf")

        seg_blocks = [
            raw_seg for raw_seg in merged
            if start_s <= raw_seg["start"] <= end_s
            or start_s <= raw_seg["end"] <= end_s
            or (raw_seg["start"] <= start_s and raw_seg["end"] >= end_s)
        ]
        seg_transcript = (
            format_transcript_for_llm(seg_blocks) if seg_blocks else full_transcript
        )

        # İlgili randevuyu bul
        appointment = next(
            (a for a in matched if a["event_id"] == seg.get("appointment_id")),
            {"event_id": seg.get("appointment_id", "unknown"), "summary": "", "start": ""},
        )

        print(f"  SOAP üretiliyor: {seg.get('patient_name', 'unknown')}")
        soap = generate_soap_note(
            transcript_segment=seg_transcript,
            patient_name=seg.get("patient_name", "unknown"),
            appointment_id=appointment["event_id"],
            appointment_summary=appointment.get("summary", ""),
            appointment_start=appointment.get("start", ""),
        )
        soap_notes.append(soap)

    # Çıktıyı JSON dosyasına kaydet
    output_path = file_path.with_suffix(".soap.json")
    output_path.write_text(
        json.dumps(soap_notes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"SOAP notları kaydedildi: {output_path}")

    return soap_notes


def run_inbox_processor():
    """
    AUDIO_INBOX_DIR klasöründeki tüm .m4a ve .mp3 dosyalarını işler.
    İşlenen dosyaları ./audio_processed/ klasörüne taşır.
    """
    AUDIO_INBOX_DIR.mkdir(parents=True, exist_ok=True)
    processed_dir = AUDIO_INBOX_DIR.parent / "audio_processed"
    processed_dir.mkdir(parents=True, exist_ok=True)

    audio_files = list(AUDIO_INBOX_DIR.glob("*.m4a")) + list(
        AUDIO_INBOX_DIR.glob("*.mp3")
    )

    if not audio_files:
        print("İşlenecek ses dosyası bulunamadı.")
        return

    calendar_service = get_calendar_service()

    all_results = []
    for audio_file in sorted(audio_files):
        try:
            soap_notes = process_audio_file(audio_file, calendar_service)
            all_results.extend(soap_notes)
            # İşlenen dosyayı taşı
            audio_file.rename(processed_dir / audio_file.name)
        except Exception as exc:
            print(f"HATA [{audio_file.name}]: {exc}")

    print(f"\nToplam işlenen hasta görüşmesi: {len(all_results)}")
    return all_results


if __name__ == "__main__":
    from logging_setup import configure_logging
    configure_logging()
    run_inbox_processor()
