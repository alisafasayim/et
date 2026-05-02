"""
Modül 2: Notion API ve Google Forms Otomasyonu

Kurulum:
    pip install notion-client google-api-python-client \
                google-auth-httplib2 google-auth-oauthlib requests

Çevre değişkenleri:
    NOTION_TOKEN          - Notion Integration secret (secret_xxx)
    NOTION_DATABASE_ID    - Hasta kayıtlarının tutulduğu Notion DB ID
    GOOGLE_FORMS_SCOPES   - Otomatik set edilir
    GOOGLE_CREDENTIALS_FILE / GOOGLE_TOKEN_FILE - OAuth2 dosyaları
"""

import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from http_retry import raise_for_retry, with_retry
from notion_schema import (
    get_database_ids,
    has_separate_sessions_db,
    is_extended,
    patient_props,
    session_props,
)

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"

# Hasta-seans hiyerarşisi: true ise hasta için DB'de TEK satır,
# her seans kök sayfanın child page'i olarak eklenir. Geriye uyum
# için varsayılan false → her seans yeni satır (eski davranış).
NOTION_HIERARCHICAL_MODE = os.getenv(
    "NOTION_HIERARCHICAL_MODE", "false"
).lower() in ("1", "true", "yes", "on")

# KVKK hibrit mod: aktifse Notion'a hasta adı/TCKN ASLA yazılmaz.
# Hasta sayfa başlığı pseudonym (#a4f9-c2b1) olur, içerikte ad/TC
# pseudonym ile değiştirilir. Tanımlayıcılar yalnızca yerel
# patient_registry'de (Türkiye'de) tutulur.
KVKK_HYBRID_MODE = os.getenv(
    "KVKK_HYBRID_MODE", "false"
).lower() in ("1", "true", "yes", "on")

GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
# Forms farklı bir Google hesabında olabilir (Calendar başka hesapta).
# GOOGLE_FORMS_TOKEN_FILE tanımlanmışsa o kullanılır, yoksa eski
# GOOGLE_TOKEN_FILE'a düşer (geriye uyum).
GOOGLE_TOKEN_FILE = os.getenv(
    "GOOGLE_FORMS_TOKEN_FILE",
    os.getenv("GOOGLE_TOKEN_FILE", "token.json"),
)
GOOGLE_FORMS_SCOPES = [
    "https://www.googleapis.com/auth/forms.responses.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


# ---------------------------------------------------------------------------
# 1. Google Forms – Anamnez Yanıtları
# ---------------------------------------------------------------------------

def get_forms_service():
    """OAuth2 akışıyla Google Forms API servisini döner."""
    creds = None
    token_path = Path(GOOGLE_TOKEN_FILE)
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_FORMS_SCOPES)

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            GOOGLE_CREDENTIALS_FILE, GOOGLE_FORMS_SCOPES
        )
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("forms", "v1", credentials=creds)


def fetch_form_responses(service, form_id: str) -> list[dict]:
    """
    Belirli bir Google Form'un tüm yanıtlarını çeker.
    Her yanıt: respondent email (varsa) + soru-cevap çiftleri.
    """
    form_meta = service.forms().get(formId=form_id).execute()
    questions = {
        item["questionItem"]["question"]["questionId"]: item.get("title", "Soru")
        for item in form_meta.get("items", [])
        if "questionItem" in item
    }

    responses_result = service.forms().responses().list(formId=form_id).execute()
    parsed = []
    for resp in responses_result.get("responses", []):
        answers = {}
        for qid, answer_obj in resp.get("answers", {}).items():
            question_text = questions.get(qid, qid)
            values = [
                v.get("value", "")
                for v in answer_obj.get("textAnswers", {}).get("answers", [])
            ]
            answers[question_text] = ", ".join(values)

        parsed.append(
            {
                "response_id": resp.get("responseId"),
                "submitted_at": resp.get("lastSubmittedTime"),
                "answers": answers,
            }
        )
    return parsed


def match_form_response_to_patient(
    responses: list[dict], patient_name: str
) -> dict | None:
    """
    Form yanıtları arasından hasta adı içeren ilkini döner.
    Formda 'Ad Soyad' veya 'İsim' adlı soru olduğu varsayılır.
    """
    name_keys = ["ad soyad", "isim", "hasta adı", "adı soyadı", "name"]
    for resp in responses:
        for question, value in resp["answers"].items():
            if any(k in question.lower() for k in name_keys):
                if patient_name.lower() in value.lower():
                    return resp
    return None


# ---------------------------------------------------------------------------
# 2. Notion API – Düşük Seviye HTTP İstemcisi
# ---------------------------------------------------------------------------

def _notion_headers() -> dict:
    """Notion API için gerekli authorization ve versiyon başlıklarını döner."""
    if not NOTION_TOKEN:
        raise EnvironmentError("NOTION_TOKEN çevre değişkeni ayarlanmamış.")
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }


@with_retry()
def _notion_post(endpoint: str, payload: dict) -> dict:
    url = f"{NOTION_BASE_URL}{endpoint}"
    response = requests.post(url, headers=_notion_headers(), json=payload, timeout=30)
    raise_for_retry(response)
    return response.json()


@with_retry()
def _notion_patch(endpoint: str, payload: dict) -> dict:
    url = f"{NOTION_BASE_URL}{endpoint}"
    response = requests.patch(url, headers=_notion_headers(), json=payload, timeout=30)
    raise_for_retry(response)
    return response.json()


# ---------------------------------------------------------------------------
# 3. Notion – Hasta Sayfası Oluşturma
# ---------------------------------------------------------------------------

def create_patient_page(
    patient_name: str,
    appointment_date: str,
    appointment_id: str,
) -> str:
    """
    Notion veritabanında yeni bir hasta sayfası oluşturur.

    Property isimleri NOTION_EXTENDED_SCHEMA env'ine göre seçilir:
      - LEGACY: 'Hasta Adı', 'Randevu Tarihi', 'Randevu ID', 'Durum'
      - EXTENDED: 'İsim', 'Durum' (+ opsiyonel ek alanlar Faz H'de)

    EXTENDED modda Sessions DB ayrıysa randevu Sessions DB'sine
    create_session_page() ile yazılır (bu fonksiyon değil çağrılır).

    Oluşturulan sayfanın page_id'sini döner.
    """
    db_ids = get_database_ids()
    target_db = db_ids.patients or NOTION_DATABASE_ID
    if not target_db:
        raise EnvironmentError(
            "NOTION_DATABASE_ID veya NOTION_PATIENTS_DB_ID set edilmemiş."
        )

    p = patient_props()
    properties: dict = {
        p.title: {"title": [{"text": {"content": patient_name}}]},
        p.status: {"select": {"name": "Arşivlendi"}},
    }
    # Legacy modda randevu metadata'sı hasta sayfasının kendisinde
    # tutulur (extended modda Sessions DB'ye yazılır).
    if not is_extended():
        properties[p.appointment_date] = {"date": {"start": appointment_date}}
        properties[p.appointment_id] = {
            "rich_text": [{"text": {"content": appointment_id}}]
        }

    payload = {"parent": {"database_id": target_db}, "properties": properties}
    result = _notion_post("/pages", payload)
    page_id = result["id"]
    print(f"  Notion sayfası oluşturuldu: {patient_name} → {page_id}")
    return page_id


def create_session_page(
    patient_page_id: str,
    patient_name: str,
    session_date: str,
    diagnosis: str = "",
) -> str:
    """
    EXTENDED schema'da Sessions DB'sine yeni seans satırı ekler.

    Hasta DB'sine relation kurar (Hastalar DB'sindeki hastayla bağlantılı).
    Geriye seans page_id'si döner (SOAP notları bu sayfaya block olarak basılır).

    EXTENDED schema kapalıysa veya Sessions DB yoksa ValueError fırlatır.
    """
    if not has_separate_sessions_db():
        raise ValueError(
            "Sessions DB yapılandırılmamış. NOTION_EXTENDED_SCHEMA=true ve "
            "NOTION_SESSIONS_DB_ID set edilmeli."
        )

    db_ids = get_database_ids()
    s = session_props()
    properties: dict = {
        s.title: {
            "title": [
                {"text": {"content": f"Seans - {session_date} - {patient_name}"}}
            ]
        },
        s.patient_relation: {"relation": [{"id": patient_page_id}]},
        s.date: {"date": {"start": session_date}},
    }
    if diagnosis and s.diagnosis:
        properties[s.diagnosis] = {
            "rich_text": [{"text": {"content": diagnosis[:2000]}}]
        }

    payload = {"parent": {"database_id": db_ids.sessions}, "properties": properties}
    result = _notion_post("/pages", payload)
    page_id = result["id"]
    print(f"  Seans sayfası oluşturuldu: {patient_name} {session_date} → {page_id}")
    return page_id


# ---------------------------------------------------------------------------
# 3b. Hasta-seans hiyerarşik kayıt (NOTION_HIERARCHICAL_MODE=true)
# ---------------------------------------------------------------------------

def find_patient_root_page(patient_name: str) -> str | None:
    """
    Hasta için kök sayfa (DB satırı) zaten var mı? Title property
    schema mod'una göre değişir ('Hasta Adı' vs 'İsim').
    Bulursa page_id döner; bulamazsa None.
    """
    db_ids = get_database_ids()
    target_db = db_ids.patients or NOTION_DATABASE_ID
    if not target_db:
        raise EnvironmentError(
            "NOTION_DATABASE_ID veya NOTION_PATIENTS_DB_ID set edilmemiş."
        )

    p = patient_props()
    payload = {
        "filter": {
            "property": p.title,
            "title": {"equals": patient_name},
        },
        "page_size": 1,
    }
    result = _notion_post(f"/databases/{target_db}/query", payload)
    pages = result.get("results", [])
    return pages[0]["id"] if pages else None


def create_patient_root_page(patient_name: str) -> str:
    """
    Hasta için kök DB satırı oluşturur. Sadece title property'si
    zorunlu; ek property'ler (telefon, TC, vs.) kullanıcı DB'de
    tanımlamışsa manuel doldurulur — bu fonksiyon basit tutar.

    Schema mod'una göre title 'Hasta Adı' veya 'İsim' olur.
    """
    db_ids = get_database_ids()
    target_db = db_ids.patients or NOTION_DATABASE_ID
    if not target_db:
        raise EnvironmentError(
            "NOTION_DATABASE_ID veya NOTION_PATIENTS_DB_ID set edilmemiş."
        )

    p = patient_props()
    payload = {
        "parent": {"database_id": target_db},
        "properties": {
            p.title: {"title": [{"text": {"content": patient_name}}]},
        },
    }
    result = _notion_post("/pages", payload)
    page_id = result["id"]
    print(f"  Hasta kök sayfası oluşturuldu: {patient_name} → {page_id}")
    return page_id


def get_or_create_patient_root_page(patient_name: str) -> str:
    """find_patient_root_page + create_patient_root_page kompozisyonu."""
    existing = find_patient_root_page(patient_name)
    if existing:
        print(f"  Mevcut hasta kök sayfası bulundu: {patient_name} → {existing}")
        return existing
    return create_patient_root_page(patient_name)


def create_session_subpage(
    patient_root_id: str,
    session_title: str,
) -> str:
    """
    Hasta kök sayfasının altına bir SEANS child page'i ekler.
    Title sayfa başlığı olur (örn: "30.04.2026 — Seans 3").
    """
    payload = {
        "parent": {"page_id": patient_root_id},
        "properties": {
            "title": [{"text": {"content": session_title}}]
        },
    }
    result = _notion_post("/pages", payload)
    page_id = result["id"]
    print(f"  Seans sayfası oluşturuldu: {session_title} → {page_id}")
    return page_id


def count_existing_session_subpages(patient_root_id: str) -> int:
    """
    Hasta kök sayfasının child block'ları arasında "child_page" tipinde
    kaç adet olduğunu sayar. Yeni seans başlığını "Seans N+1" olarak
    numaralandırmak için kullanılır.
    """
    try:
        endpoint = f"/blocks/{patient_root_id}/children?page_size=100"
        url = f"{NOTION_BASE_URL}{endpoint}"
        resp = requests.get(url, headers=_notion_headers(), timeout=30)
        raise_for_retry(resp)
        blocks = resp.json().get("results", [])
        return sum(1 for b in blocks if b.get("type") == "child_page")
    except Exception as exc:
        # Sayım başarısız olursa numarayı atla (başlığa "Seans" yazılır)
        logger = __import__("logging").getLogger("notion_archiver")
        logger.warning("Seans sayımı yapılamadı: %s", exc)
        return -1


# ---------------------------------------------------------------------------
# 4. Notion Block Yardımcıları
# ---------------------------------------------------------------------------

def _rich_text(text: str) -> list[dict]:
    """
    Notion'da her bir text node max 2000 karakter kabul eder.
    Uzun metni 2000'lik chunk'lara böler — eskiden text[:2000]
    ile sessizce kırpılıyordu, anamnez/SOAP cevapları kaybediliyordu.
    """
    if not text:
        return [{"type": "text", "text": {"content": "—"}}]
    return [
        {"type": "text", "text": {"content": text[i : i + 2000]}}
        for i in range(0, len(text), 2000)
    ]


def _heading2(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        # Heading'ler için 2000 karakter pratikte yeter; başlık kısa olur.
        "heading_2": {"rich_text": _rich_text(text[:2000])},
    }


def _heading3(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": _rich_text(text[:2000])},
    }


def _paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(text)},
    }


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _callout(text: str, emoji: str = "ℹ️") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich_text(text),
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


def _append_blocks(page_id: str, blocks: list[dict]) -> None:
    """
    Notion block append API'si tek seferde max 100 block kabul eder.
    Listeyi 100'lük parçalara bölerek gönderir.
    """
    for i in range(0, len(blocks), 100):
        chunk = blocks[i : i + 100]
        _notion_patch(f"/blocks/{page_id}/children", {"children": chunk})


# ---------------------------------------------------------------------------
# 5. Anamnez Verilerini Sayfaya Basma
# ---------------------------------------------------------------------------

def append_anamnesis_to_page(page_id: str, form_response: dict | None) -> None:
    """
    Google Forms anamnez yanıtlarını Notion sayfasına alt başlıklar halinde yazar.
    """
    blocks: list[dict] = [
        _heading2("📋 Anamnez Formu"),
    ]

    if not form_response:
        blocks.append(_callout("Bu hasta için doldurulmuş anamnez formu bulunamadı.", "⚠️"))
        _append_blocks(page_id, blocks)
        return

    blocks.append(
        _paragraph(f"Form yanıt ID: {form_response.get('response_id', '—')}")
    )
    blocks.append(
        _paragraph(f"Gönderilme tarihi: {form_response.get('submitted_at', '—')}")
    )
    blocks.append(_divider())

    for question, answer in form_response.get("answers", {}).items():
        blocks.append(_heading3(question))
        blocks.append(_paragraph(answer if answer else "—"))

    _append_blocks(page_id, blocks)
    print(f"  Anamnez formu sayfaya eklendi ({len(form_response.get('answers', {}))} soru)")


# ---------------------------------------------------------------------------
# 6. SOAP Notlarını Sayfaya Basma
# ---------------------------------------------------------------------------

SOAP_SECTION_MAP = {
    "subjective": {
        "label": "🗣️ Subjektif (Şikayet & Öykü)",
        "fields": {
            "chief_complaint": "Ana Şikayet",
            "history_of_present_illness": "Mevcut Hastalık Öyküsü",
            "family_history": "Aile Öyküsü",
            "developmental_history": "Gelişimsel Öykü",
        },
    },
    "objective": {
        "label": "🔍 Objektif (Muayene Bulguları)",
        "fields": {
            "mental_status_exam": "Ruhsal Durum Muayenesi",
            "behavior_observations": "Davranış Gözlemleri",
            "affect_mood": "Duygudurum ve Afekt",
        },
    },
    "assessment": {
        "label": "🧠 Değerlendirme (Tanı)",
        "fields": {
            "provisional_diagnosis": "Ön Tanı",
            "differential_diagnosis": "Ayırıcı Tanı",
            "risk_assessment": "Risk Değerlendirmesi",
        },
    },
    "plan": {
        "label": "📝 Plan & Tedavi",
        "fields": {
            "medication": "İlaç Tedavisi",
            "therapy": "Psikoterapi Planı",
            "parent_guidance": "Aile/Veli Yönlendirmesi",
            "follow_up": "Takip Planı",
            "referrals": "Yönlendirmeler",
        },
    },
}


def append_soap_to_page(page_id: str, soap_note: dict) -> None:
    """
    Modül 1'den gelen SOAP JSON'unu Notion sayfasına hiyerarşik bloklar halinde yazar.
    """
    generated_at = soap_note.get("generated_at", "—")
    blocks: list[dict] = [
        _divider(),
        _heading2("🏥 Klinik Not (SOAP)"),
        _paragraph(f"Oluşturulma tarihi: {generated_at}"),
        _divider(),
    ]

    soap = soap_note.get("soap", {})
    for section_key, section_meta in SOAP_SECTION_MAP.items():
        section_data = soap.get(section_key, {})
        blocks.append(_heading2(section_meta["label"]))

        for field_key, field_label in section_meta["fields"].items():
            value = section_data.get(field_key, "")
            if value:
                blocks.append(_heading3(field_label))
                blocks.append(_paragraph(value))

        blocks.append(_divider())

    _append_blocks(page_id, blocks)
    print(f"  SOAP notu sayfaya eklendi ({list(soap.keys())} bölümleri)")


# ---------------------------------------------------------------------------
# 7. Ana Orkestratör
# ---------------------------------------------------------------------------

def _resolve_appointment_date(soap_note: dict) -> str:
    """
    Randevu tarihi sırasıyla şu kaynaklardan alınır:
      1. soap_note["appointment_start"]  → M1'in Calendar'dan aldığı gerçek değer
      2. soap_note["generated_at"]       → fallback (SOAP üretim zamanı)
      3. datetime.now()                  → son çare
    Önceki sürüm sadece (2)'yi kullanıyordu → klinik kayıtta yanlış tarih.
    """
    raw = (
        soap_note.get("appointment_start")
        or soap_note.get("generated_at")
        or datetime.now().isoformat()
    )
    return raw[:10]  # YYYY-MM-DD


def _archive_flat(
    soap_note: dict,
    form_response: dict | None,
) -> str:
    """Eski davranış: her seans yeni DB satırı."""
    real_patient_name = soap_note.get("patient_name", "Bilinmeyen Hasta")
    appointment_id = soap_note.get("appointment_id", "unknown")
    appointment_date = _resolve_appointment_date(soap_note)

    if KVKK_HYBRID_MODE:
        # Flat + hibrit: her seans için yeni satır ama pseudonym ile
        from patient_registry import get_default_registry
        from pii_crypto import short_pseudonym

        registry = get_default_registry()
        existing = registry.find_by_name(real_patient_name)
        if existing:
            patient_uuid = existing[0]["uuid"]
        else:
            patient_uuid = registry.create_patient(full_name=real_patient_name)
        display_name = short_pseudonym(patient_uuid)
        soap_note = _scrub_soap_pii(soap_note, real_patient_name, display_name)
        if form_response:
            form_response = _scrub_form_pii(form_response, real_patient_name, display_name)
    else:
        display_name = real_patient_name

    page_id = create_patient_page(display_name, appointment_date, appointment_id)
    append_anamnesis_to_page(page_id, form_response)
    append_soap_to_page(page_id, soap_note)
    return page_id


def _scrub_soap_pii(
    soap_note: dict,
    real_name: str,
    pseudonym: str,
) -> dict:
    """
    SOAP içindeki tüm metin alanlarında hasta adının geçtiği yerleri
    pseudonym ile değiştirir. Telefon ve TCKN'yi de logging_setup'taki
    redact_pii ile yıkar. Defansif kopyalama: orijinal soap_note
    bozulmaz.

    NOT: Bu yalnızca KESİN tanımlayıcıları temizler; LLM'in ürettiği
    metinde dolaylı tanımlayıcılar (okul adı, mahalle vs.) varsa
    onlar yakalanmaz. K-anonimite garantisi DEĞİLDİR; KVKK gözünde
    "kişiyi makul ölçüde belirleyemeyecek anonim hale getirilmiş
    veri" hedeflenir, mutlak anonimleştirme değil.
    """
    import copy

    from logging_setup import redact_pii

    scrubbed = copy.deepcopy(soap_note)
    scrubbed["patient_name"] = pseudonym

    soap = scrubbed.get("soap", {})
    patterns = _build_name_patterns(real_name, pseudonym)
    for section in ("subjective", "objective", "assessment", "plan"):
        section_dict = soap.get(section, {}) or {}
        for k, v in list(section_dict.items()):
            if not isinstance(v, str) or not v:
                continue
            v = _apply_name_patterns(v, patterns, pseudonym)
            v = redact_pii(v)
            section_dict[k] = v
    return scrubbed


def _scrub_form_pii(
    form_response: dict,
    real_name: str,
    pseudonym: str,
) -> dict:
    """Anamnez form yanıtlarında hasta adı + PII yıkama."""
    import copy

    from logging_setup import redact_pii

    scrubbed = copy.deepcopy(form_response)
    patterns = _build_name_patterns(real_name, pseudonym)

    answers = scrubbed.get("answers", {})
    for q, a in list(answers.items()):
        if not isinstance(a, str) or not a:
            continue
        a = _apply_name_patterns(a, patterns, pseudonym)
        a = redact_pii(a)
        answers[q] = a
    return scrubbed


# Hasta adı pattern'leri:
#   1. Tam ad ("Ali Yıldız Demir")
#   2. Her ad parçası ≥4 karakter ("Yıldız", "Demir") — kısa adlar
#      (Ali, Eda) yaygın olduğundan atlanır; aksi halde false
#      positive (örn: "Ali Baba kuyruklu yıldız" cümlesi).
def _build_name_patterns(real_name: str, pseudonym: str) -> list:
    import re
    if not real_name:
        return []
    patterns = [(re.compile(re.escape(real_name), re.IGNORECASE), pseudonym)]
    parts = [p for p in real_name.split() if len(p) >= 4]
    for p in parts:
        # \b kelime sınırı; "Yıldızlar" gibi türevleri yakalamasın
        patterns.append((re.compile(rf"\b{re.escape(p)}\b", re.IGNORECASE), pseudonym))
    return patterns


def _apply_name_patterns(text: str, patterns: list, pseudonym: str) -> str:
    for pat, replacement in patterns:
        text = pat.sub(replacement, text)
    return text


def _resolve_patient_root(real_patient_name: str) -> tuple[str, str]:
    """
    KVKK_HYBRID_MODE'a göre kök sayfa ve Notion'a YAZILACAK adı çözer.

    Döner: (notion_root_page_id, display_name_for_notion)
        - hibrit aktif: display = pseudonym (#a4f9-c2b1)
        - hibrit pasif: display = gerçek hasta adı (eski davranış)

    Hibrit mod'da yerel patient_registry'den UUID bulunur veya oluşturulur;
    Notion kök sayfası registry'de cached ise yeniden kullanılır,
    aksi halde yeni oluşturulur ve registry'ye bağlanır.
    """
    if not KVKK_HYBRID_MODE:
        # Eski davranış: gerçek isimle Notion'a yaz
        return get_or_create_patient_root_page(real_patient_name), real_patient_name

    # Hibrit mod: tüm PII yerelde kalır
    from patient_registry import get_default_registry
    from pii_crypto import short_pseudonym

    registry = get_default_registry()
    matches = registry.find_by_name(real_patient_name)
    if matches:
        if len(matches) > 1:
            # Aynı isimli birden fazla — manuel disambiguation gerekebilir.
            # Şimdilik en yenisini al; admin paneli üzerinden düzeltilebilir.
            patient = matches[0]
        else:
            patient = matches[0]
        patient_uuid = patient["uuid"]
        existing_notion = patient.get("notion_page_id") or ""
    else:
        patient_uuid = registry.create_patient(full_name=real_patient_name)
        existing_notion = ""

    pseudonym = short_pseudonym(patient_uuid)

    if existing_notion:
        return existing_notion, pseudonym

    # Notion kök sayfasını pseudonym ile oluştur
    root_id = create_patient_root_page(pseudonym)
    registry.attach_notion_page(patient_uuid, root_id)
    return root_id, pseudonym


def _archive_hierarchical(
    soap_note: dict,
    form_response: dict | None,
) -> str:
    """
    NOTION_HIERARCHICAL_MODE=true: hasta için TEK kök sayfa (DB satırı),
    her seans bu kökün altında child page olarak.

    KVKK_HYBRID_MODE=true: Notion'a yazılan tüm metinde hasta adı
    pseudonym (#a4f9-c2b1) ile değiştirilir; gerçek ad yalnızca
    yerel patient_registry'de.

    İlk seansta anamnez kök sayfaya bir kez yazılır; sonraki seanslar
    sadece SOAP içerir.
    """
    real_patient_name = soap_note.get("patient_name", "Bilinmeyen Hasta")
    appointment_date = _resolve_appointment_date(soap_note)

    print(f"\nArşivleniyor (hiyerarşik): {real_patient_name}")

    # 1. Hasta kök sayfası + Notion'a görünecek ad
    root_id, display_name = _resolve_patient_root(real_patient_name)

    # 2. SOAP içeriğindeki PII'yi pseudonym ile değiştir (yalnızca hibrit mod)
    if KVKK_HYBRID_MODE:
        soap_note = _scrub_soap_pii(soap_note, real_patient_name, display_name)
        if form_response:
            form_response = _scrub_form_pii(form_response, real_patient_name, display_name)

    # 2. Anamnez yalnızca ilk seansta kök sayfaya eklenir
    existing_count = count_existing_session_subpages(root_id)
    if existing_count == 0 and form_response:
        # İlk seans → anamnezi kök sayfaya bas
        append_anamnesis_to_page(root_id, form_response)
    elif existing_count == 0 and not form_response:
        # Yine de bilgilendirici callout
        _append_blocks(root_id, [
            _heading2("📋 Anamnez Formu"),
            _callout("Bu hasta için doldurulmuş anamnez formu bulunamadı.", "⚠️"),
        ])

    # 3. Seans numaralandırma (mevcut child_page sayısı + 1)
    session_no = (existing_count + 1) if existing_count >= 0 else 0
    title_parts = [appointment_date]
    if session_no > 0:
        title_parts.append(f"Seans {session_no}")
    session_title = " — ".join(title_parts)

    # 4. Seans child page'ini oluştur, SOAP'ı içine yaz
    session_id = create_session_subpage(root_id, session_title)
    append_soap_to_page(session_id, soap_note)

    print(f"  Tamamlandı → Notion session_page_id: {session_id} (kök: {root_id})")
    # Caller için: yeni oluşturulan SEANS sayfasının id'sini döndürürüz
    # (eski API ile uyumlu, "yeni eklenen kayıt" mantığı korunur).
    return session_id


def archive_patient_session(
    soap_note: dict,
    form_id: str,
    forms_service=None,
    all_form_responses: list[dict] | None = None,
) -> str:
    """
    Tek bir hasta seansını Notion'a arşivler.

    NOTION_HIERARCHICAL_MODE=false (varsayılan): her seans yeni DB satırı.
    NOTION_HIERARCHICAL_MODE=true: hasta için tek kök, seanslar child page.

    Döner: Oluşturulan / kullanılan Notion page_id (seans sayfası).
    """
    patient_name = soap_note.get("patient_name", "Bilinmeyen Hasta")

    # Form yanıtını bir kez çöz
    if all_form_responses is None and forms_service and form_id:
        all_form_responses = fetch_form_responses(forms_service, form_id)
    form_response = None
    if all_form_responses:
        form_response = match_form_response_to_patient(all_form_responses, patient_name)

    # Extended schema + Sessions DB varsa Sessions DB'sine yeni satır
    # aç ve SOAP'ı oraya yaz (hasta sayfasına block koyma — schema farklı).
    if has_separate_sessions_db():
        return _archive_extended(soap_note, form_response)
    if NOTION_HIERARCHICAL_MODE:
        return _archive_hierarchical(soap_note, form_response)
    return _archive_flat(soap_note, form_response)


def _archive_extended(soap_note: dict, form_response: dict | None) -> str:
    """
    EXTENDED schema arşivleme: hasta Hastalar DB'sinde tek satır,
    her seans Sessions DB'sinde ayrı satır + Hastalar relation.

    SOAP block'ları seans sayfasına basılır. KVKK hibrit modda
    Notion'a sadece pseudonym yazılır (gerçek isim/TCKN yerel
    patient_registry'de şifreli kalır).

    Anamnez form yanıtı EXTENDED schema'da Form Responses DB'sine
    aittir — bu fonksiyon onu seans sayfasına block olarak basıp
    geçer; ayrı Forms DB'ye yazma Faz H/sonra eklenir.
    """
    patient_name = soap_note.get("patient_name", "Bilinmeyen Hasta")

    # _resolve_patient_root mevcut KVKK + registry mantığını kapsar
    patient_root_id, display_name = _resolve_patient_root(patient_name)

    # Seans tarihi: SOAP'ın gerçek randevu zamanı (M1 timezone fix
    # sonrası güvenilir) veya generated_at fallback
    session_date = _resolve_appointment_date(soap_note)

    diagnosis = (
        soap_note.get("soap", {})
        .get("assessment", {})
        .get("provisional_diagnosis", "")
    )

    # Sessions DB'de yeni satır + relation
    session_page_id = create_session_page(
        patient_page_id=patient_root_id,
        patient_name=display_name,
        session_date=session_date,
        diagnosis=diagnosis,
    )

    # SOAP block'larını seans sayfasına bas
    append_soap_to_page(session_page_id, soap_note)

    # Anamnez varsa ayrıca seans sayfasına ekle (kompakt görünüm)
    if form_response:
        append_anamnesis_to_page(session_page_id, form_response)

    return session_page_id


def archive_all_soap_files(
    soap_dir: Path,
    form_id: str,
) -> list[dict]:
    """
    Belirli bir klasördeki tüm .soap.json dosyalarını okuyup Notion'a arşivler.
    Form yanıtlarını bir kez çekip tüm hastalar için yeniden kullanır.
    """
    soap_files = list(soap_dir.glob("*.soap.json"))
    if not soap_files:
        print("Arşivlenecek SOAP dosyası bulunamadı.")
        return []

    forms_service = get_forms_service()
    print(f"Google Forms yanıtları çekiliyor (form_id={form_id})...")
    all_form_responses = fetch_form_responses(forms_service, form_id) if form_id else []
    print(f"  {len(all_form_responses)} form yanıtı bulundu")

    results = []
    for soap_file in sorted(soap_files):
        soap_notes = json.loads(soap_file.read_text(encoding="utf-8"))
        for soap_note in soap_notes:
            try:
                page_id = archive_patient_session(
                    soap_note=soap_note,
                    form_id=form_id,
                    all_form_responses=all_form_responses,
                )
                results.append({"file": str(soap_file), "page_id": page_id, "status": "ok"})
            except Exception as exc:
                print(f"HATA [{soap_file.name}]: {exc}")
                results.append({"file": str(soap_file), "error": str(exc), "status": "failed"})

    print(f"\nToplam arşivlenen: {sum(1 for r in results if r['status'] == 'ok')}/{len(results)}")
    return results


if __name__ == "__main__":
    import argparse

    from logging_setup import configure_logging
    configure_logging()

    parser = argparse.ArgumentParser(description="Notion Arşivleyici")
    parser.add_argument(
        "--soap-dir",
        default="./audio_processed",
        help="SOAP JSON dosyalarının bulunduğu klasör",
    )
    parser.add_argument(
        "--form-id",
        default=os.getenv("GOOGLE_ANAMNESIS_FORM_ID", ""),
        help="Anamnez Google Form ID",
    )
    args = parser.parse_args()

    archive_all_soap_files(
        soap_dir=Path(args.soap_dir),
        form_id=args.form_id,
    )
