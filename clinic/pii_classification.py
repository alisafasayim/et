"""
KVKK PII Sınıflandırması — Form Yanıtları

Bu modül form sorularının PII (Personal Identifiable Information) /
Klinik veri olarak sınıflandırmasını yapar. KVKK m.6 (özel nitelikli
sağlık verisi) + m.9 (yurtdışı transfer) için kritik.

Kategoriler:
- PII (yerel-only): TC, ad, telefon, adres, anne-baba ad/meslek, okul adı
  → Sadece patient_registry.db'ye Fernet ile şifreli yazılır
- KLINIK (Notion OK): Doğum yılı, sınıf, semptom, ilaç, gelişim öyküsü
  → Notion form response sayfasına block olarak yazılabilir

Soru başlıkları küçük harfe çevrilip pattern matching ile karşılaştırılır.
Listede yer almayan sorular **default olarak PII sayılır** (güvenli yol).

Kullanım:
    from pii_classification import classify_question, redact_pii

    answers = {"Adı Soyadı": "Ali Veli", "Sınıf": "1. sınıf", "Şikayet": "..."}
    clinical_only = redact_pii(answers)
    # clinical_only = {"Sınıf": "1. sınıf", "Şikayet": "..."}
"""

from __future__ import annotations

import re
from typing import Literal

Category = Literal["PII", "KLINIK", "GRI"]

# ---------------------------------------------------------------------------
# 1. KESİN PII — Notion'a ASLA yazılmaz
# ---------------------------------------------------------------------------
# Pattern matching: soru başlığı küçük harfli haline bu pattern'lardan biri
# eşleşirse PII sayılır. Tam eşleşme değil, "içerir" mantığı.

PII_PATTERNS: list[str] = [
    # İsim/kimlik
    "ad soyad", "adı soyadı", "adi soyadi",
    "adınız", "adiniz", "isminiz", "ismi",
    "tc kimlik", "t.c. kimlik", "tc kimli",
    "kimlik numara", "kimlik no",

    # İletişim
    "telefon", "cep telefonu", "gsm",
    "e-posta", "email", "e-mail", "eposta",

    # Adres
    "adres", "ev adresi", "ikamet",
    "il/ilçe", "il-ilçe", "şehir",
    "posta kodu",

    # Doğum (tam tarih)
    "doğum tarihi", "dogum tarihi",  # Yıl ayrı kategoride

    # Aile bireyleri (ad/isim/iletişim alanları)
    "annenin adı", "annenin adi", "anne adı", "anne adi",
    "babanın adı", "babanin adi", "baba adı", "baba adi",
    "annenin telefonu", "babanın telefonu",
    "kardeş adı", "kardes adi",
    "evde yaşayan", "evde yasayan",  # isim içerir

    # Mesleki / ekonomik (kullanıcı tercihi: P)
    "annenin mesleği", "annenin meslegi", "anne mesleği",
    "babanın mesleği", "babanin meslegi", "baba mesleği",

    # Eğitim kurumu (isim → identifier)
    "okul/yuva", "okul adı", "okul adi", "okul/kreş",
    "yuva adı", "kreş adı",

    # Bakım veren — isim içerebilir, güvenli için P
    "bakım veren", "bakim veren", "kim ve ne kadar süreli",

    # Form dolduran
    "formu dolduran", "formu dolduranın",
    "doldur",  # "Formu dolduranın..."

    # Cinsiyet — KVKK m.6 özel nitelikli demografik
    "cinsiyet",
]

# ---------------------------------------------------------------------------
# 2. KESİN KLİNİK — Notion'a yazılır (PII'siz, klinik bağlam)
# ---------------------------------------------------------------------------

KLINIK_PATTERNS: list[str] = [
    # Klinik anamnez
    "şikayet", "sikayet", "başvuru nedeni", "basvuru nedeni",
    "neden", "yardım talep", "danışma",

    # Tanı/tedavi geçmişi
    "kullandığı ilaç", "kullandigi ilac", "ilaç", "ilac",
    "geçirdiği hastalık", "gecirdigi hastalik",
    "doktor", "tanı", "tani", "teşhis",
    "ameliyat", "operasyon",

    # Gelişim
    "gelişim", "gelisim", "gelişim basamak",
    "yürüme yaşı", "yuru me yasi", "yürüme",
    "konuşma yaşı", "konusma yasi", "konuşma",
    "tuvalet", "altını", "altini",
    "emekleme", "ilk söz", "ilk soz",

    # Davranış
    "davranış", "davranis", "tepki",
    "uyku", "iştah", "istah", "yeme",
    "korku", "kaygı", "kaygi",

    # Sınıf seviyesi (kategorik)
    "sınıf seviy", "sinif seviy",
    "kaçıncı sınıf", "kacinci sinif",

    # Doğum yılı (sadece yıl, kullanıcı kararı: K)
    "doğum yılı", "dogum yili",

    # Çevresel klinik faktörler
    "sigara", "alkol", "madde",
    "evde sigara", "evde alkol",

    # Genetik/aile klinik faktörü (kullanıcı kararı: K)
    # Akraba evliliği konjenital nörogelişimsel bozuklukların prevalansı
    # için klinik veri (kategorik evet/hayır → kimliklendirici değil).
    "akraba", "akrabalık", "akrabalik",
]


def classify_question(question: str) -> Category:
    """
    Bir soru başlığını kategorize eder: PII / KLINIK / GRI.

    Algoritma:
    1. Soru lower-case'e çevrilir
    2. PII_PATTERNS'da herhangi biri eşleşirse → PII
    3. KLINIK_PATTERNS'da eşleşirse → KLINIK
    4. Hiçbiri yoksa → PII (güvenli default — bilinmeyeni yerel tut)

    Bu "PII default" stratejisi tehlikeli alanların kaçırılmamasını
    sağlar; bilinen klinik alanları açıkça işaretlemek gerekir.
    """
    q = (question or "").lower().strip()
    if not q:
        return "PII"

    for pat in PII_PATTERNS:
        if pat in q:
            return "PII"

    for pat in KLINIK_PATTERNS:
        if pat in q:
            return "KLINIK"

    # Default: bilinmeyen sorular güvenli için PII
    return "PII"


# ---------------------------------------------------------------------------
# 3. İçerik bazlı PII tespiti (yedek katman)
# ---------------------------------------------------------------------------

# 11 haneli rakam = TC kimlik (ardışık, başında 0 yok)
TC_REGEX = re.compile(r"\b[1-9]\d{10}\b")
# Türk telefon (0 5XX XXX XX XX, +90 5XX, 5XX XXX XX XX)
PHONE_REGEX = re.compile(
    r"(?:\+?9?0?\s*)?5\d{2}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}"
)
# Tam tarih (DD.MM.YYYY veya DD/MM/YYYY veya DD-MM-YYYY, 19xx-20xx)
FULL_DATE_REGEX = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2}\b")


def contains_pii_content(answer: str) -> bool:
    """
    Cevap içeriğinde PII pattern'ı var mı (TC, telefon, tam tarih)?

    Bu yedek bir katman: soru klinik kategoride bile olsa, cevapta
    yanlışlıkla PII varsa (örn. 'Şikayet' alanına 'Adım Ali, TC...'
    yazılmışsa) yakalanır.
    """
    if not answer:
        return False
    if TC_REGEX.search(answer):
        return True
    if PHONE_REGEX.search(answer):
        return True
    if FULL_DATE_REGEX.search(answer):
        return True
    return False


# ---------------------------------------------------------------------------
# 4. Ana redaction fonksiyonu
# ---------------------------------------------------------------------------

def redact_pii(
    answers: dict[str, str],
    *,
    return_pii: bool = False,
) -> dict[str, str]:
    """
    Form yanıtlarını filtreler:
    - Sadece KLINIK kategorisindeki sorular döner (Notion'a yazılır)
    - PII soruları + içeriğinde PII bulunanlar atılır

    return_pii=True ise PII alanlarını döndürür (yerel kayıt için).

    Args:
        answers: {soru: cevap} dict
        return_pii: True olursa PII alanlarını döner, klinik değil

    Returns:
        Filtrelenmiş dict — KLINIK alanlar (default) veya PII alanlar.
    """
    clinical: dict[str, str] = {}
    pii: dict[str, str] = {}

    for question, answer in (answers or {}).items():
        category = classify_question(question)
        # Cevap içeriğinde PII varsa kategori PII'ye degrade
        if category == "KLINIK" and contains_pii_content(answer):
            category = "PII"

        if category == "PII":
            pii[question] = answer
        else:
            clinical[question] = answer

    return pii if return_pii else clinical
