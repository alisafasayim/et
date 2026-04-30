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

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_API_VERSION = "2022-06-28"
NOTION_BASE_URL = "https://api.notion.com/v1"

GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
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


def _notion_post(endpoint: str, payload: dict) -> dict:
    url = f"{NOTION_BASE_URL}{endpoint}"
    response = requests.post(url, headers=_notion_headers(), json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def _notion_patch(endpoint: str, payload: dict) -> dict:
    url = f"{NOTION_BASE_URL}{endpoint}"
    response = requests.patch(url, headers=_notion_headers(), json=payload, timeout=30)
    response.raise_for_status()
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
    Veritabanının şu property'lere sahip olduğu varsayılır:
      - 'Hasta Adı'  (title)
      - 'Randevu Tarihi' (date)
      - 'Randevu ID'  (rich_text)
      - 'Durum'       (select)
    Oluşturulan sayfanın page_id'sini döner.
    """
    if not NOTION_DATABASE_ID:
        raise EnvironmentError("NOTION_DATABASE_ID çevre değişkeni ayarlanmamış.")

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Hasta Adı": {
                "title": [{"text": {"content": patient_name}}]
            },
            "Randevu Tarihi": {
                "date": {"start": appointment_date}
            },
            "Randevu ID": {
                "rich_text": [{"text": {"content": appointment_id}}]
            },
            "Durum": {
                "select": {"name": "Arşivlendi"}
            },
        },
    }

    result = _notion_post("/pages", payload)
    page_id = result["id"]
    print(f"  Notion sayfası oluşturuldu: {patient_name} → {page_id}")
    return page_id


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

def archive_patient_session(
    soap_note: dict,
    form_id: str,
    forms_service=None,
    all_form_responses: list[dict] | None = None,
) -> str:
    """
    Tek bir hasta seansını Notion'a arşivler:
      1. Hasta sayfası oluştur
      2. Anamnez formunu ekle
      3. SOAP notunu ekle

    Parametre olarak önceden çekilmiş form yanıtları verilebilir
    (aynı form için tekrar API çağrısı yapmamak için).

    Döner: Oluşturulan Notion page_id
    """
    patient_name = soap_note.get("patient_name", "Bilinmeyen Hasta")
    appointment_id = soap_note.get("appointment_id", "unknown")

    # Randevu tarihi sırasıyla şu kaynaklardan alınır:
    #   1. soap_note["appointment_start"]  → M1'in Calendar'dan aldığı gerçek değer
    #   2. soap_note["generated_at"]       → fallback (SOAP üretim zamanı)
    #   3. datetime.now()                  → son çare
    # Önceki sürüm sadece (2)'yi kullanıyordu; bu, klinik kayıtta randevu
    # tarihi yerine SOAP üretim zamanını yazıyordu (saatler/günler farkedebilir).
    appointment_start_raw = (
        soap_note.get("appointment_start")
        or soap_note.get("generated_at")
        or datetime.now().isoformat()
    )
    # Sadece YYYY-MM-DD kısmını al
    appointment_date = appointment_start_raw[:10]

    print(f"\nArşivleniyor: {patient_name}")

    # 1. Notion sayfası oluştur
    page_id = create_patient_page(patient_name, appointment_date, appointment_id)

    # 2. Form yanıtlarını çek / kullan
    if all_form_responses is None and forms_service and form_id:
        all_form_responses = fetch_form_responses(forms_service, form_id)

    form_response = None
    if all_form_responses:
        form_response = match_form_response_to_patient(all_form_responses, patient_name)

    # 3. Anamnezi sayfaya bas
    append_anamnesis_to_page(page_id, form_response)

    # 4. SOAP'ı sayfaya bas
    append_soap_to_page(page_id, soap_note)

    print(f"  Tamamlandı → Notion page_id: {page_id}")
    return page_id


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
