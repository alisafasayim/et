"""
Klinik risk tespiti ve doktora acil WhatsApp push.

SOAP not'taki risk_assessment alanı + tüm subjective/objective alanları
taranıp kritik anahtar kelime bulunursa doktora anında bildirim gider.
LLM çıktısının doğruluğuna güvenilemez (hayati riskli), bu yüzden
deterministik regex tabanlı taraması yapılır.

Severity:
  - critical: aktif intihar/zarar niyeti veya plan
  - high    : pasif intihar düşüncesi, ölüm fikri
  - none    : risk içeren ifade tespit edilmedi
"""

import logging
import os
import re
from typing import Optional

from state_store import get_default_store

logger = logging.getLogger("risk_alerts")

DOCTOR_PHONE = os.getenv("DOCTOR_PHONE", "")

# ---------------------------------------------------------------------------
# Anahtar kelime kalıpları
# ---------------------------------------------------------------------------
# Türkçe + İngilizce. Sahte pozitif yapsa bile maliyeti düşük; sahte
# negatif maliyeti hayat. Bu yüzden gevşek tutuyoruz.
# ---------------------------------------------------------------------------

# Türkçe morfolojisi nedeniyle pattern'lar gevşek tutuluyor (sahte
# pozitif maliyet düşük, sahte negatif maliyet hayat).
CRITICAL_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"intihar\s*(plan|girişim|niyet|teşebbüs)",
        r"\b(kendini|kendisini)\s*öldür",
        r"hap(lar)?(ı|ları)?\s*(iç|yut)",
        r"bilek\s*kes",
        r"kesici\s*alet",
        r"silah(la|ı)?\s*(kafa|baş)",
        # "iple asıl", "ip ile asma", "asma niyeti" + ip yakınlığı
        r"\bip(le)?\s+(ile\s+)?as(ıl|ma|arak)",
        r"asma\s+niyet",
        r"yaşamı(mı|nı)?\s*sonland",
        r"\bsuicid(e|al)\s*(plan|attempt|intent)",
        r"self[-\s]?harm",
    ]
]

HIGH_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"intihar\s*(düşünc|fikr|fikir)",
        r"ölüm\s*(düşünc|fikr|fikir|isteği)",
        r"ölmek\s*ist",
        # istemi, isteme, istemed → "istem" prefix yakalar
        r"yaşamak\s*istem",
        r"\bkendine\s*zarar",
        r"özsaldır",
        r"yaşa(masaydım|masam)",
        r"hayat(ım|ımın)?\s*anlamı\s*yok",
        r"suicid(e|al)\s*(thought|ideation)",
    ]
]


# ---------------------------------------------------------------------------
# Tespit
# ---------------------------------------------------------------------------

def _scan_text(text: str) -> tuple[str, list[str]]:
    """Verilen metni tarar, en yüksek severity ve eşleşen kalıpları döner."""
    if not text:
        return "none", []

    matched_critical = [p.pattern for p in CRITICAL_PATTERNS if p.search(text)]
    if matched_critical:
        return "critical", matched_critical

    matched_high = [p.pattern for p in HIGH_PATTERNS if p.search(text)]
    if matched_high:
        return "high", matched_high

    return "none", []


def detect_risk(soap_note: dict) -> dict:
    """
    SOAP içindeki tüm metinsel alanları (özellikle assessment.risk_assessment,
    subjective.* ve plan.*) tarayıp risk seviyesi döner.

    Döner: {"level": "critical"|"high"|"none", "matched": [...], "snippets": [...]}
    """
    soap = soap_note.get("soap", {})

    # Risk için en kritik alanlar; ayrıca tüm SOAP alt alanlarını topluca tarar
    candidates: list[tuple[str, str]] = []
    risk_text = soap.get("assessment", {}).get("risk_assessment", "")
    if risk_text:
        candidates.append(("assessment.risk_assessment", risk_text))

    for section in ("subjective", "objective", "plan"):
        for field, value in soap.get(section, {}).items():
            if isinstance(value, str) and value:
                candidates.append((f"{section}.{field}", value))

    overall_level = "none"
    matched: list[str] = []
    snippets: list[dict] = []

    for path, text in candidates:
        level, patterns = _scan_text(text)
        if level == "none":
            continue
        snippets.append({"path": path, "level": level, "text": text[:200]})
        matched.extend(patterns)
        # critical > high > none
        if level == "critical":
            overall_level = "critical"
        elif level == "high" and overall_level != "critical":
            overall_level = "high"

    return {
        "level": overall_level,
        "matched": sorted(set(matched)),
        "snippets": snippets,
    }


# ---------------------------------------------------------------------------
# Doktor bildirimi
# ---------------------------------------------------------------------------

def send_risk_alert(
    soap_note: dict,
    risk: dict,
    doctor_phone: Optional[str] = None,
) -> bool:
    """
    Risk varsa doktora WhatsApp ile acil push gönderir.
    Idempotent: aynı (appointment_id, level) kombinasyonu için tekrar
    göndermez. Döner: True (gönderildi) / False (atlandı / risk yok).
    """
    if risk["level"] == "none":
        return False

    phone = doctor_phone or DOCTOR_PHONE
    if not phone:
        logger.warning(
            "RİSK TESPİT EDİLDİ ama DOCTOR_PHONE ayarlanmamış (level=%s, hasta=%s)",
            risk["level"], soap_note.get("patient_name"),
        )
        return False

    appt_id = soap_note.get("appointment_id", "unknown")
    dedup_key = f"{appt_id}:{risk['level']}"
    store = get_default_store()
    if not store.claim("risk_alert", dedup_key, meta=soap_note.get("patient_name")):
        logger.info("Risk alarmı zaten gönderildi: %s", dedup_key)
        return False

    icon = "🚨" if risk["level"] == "critical" else "⚠️"
    snippets_text = "\n".join(
        f"- {s['path']}: {s['text']}" for s in risk["snippets"][:3]
    )
    message = (
        f"{icon} ACİL — KLİNİK RİSK\n"
        f"Hasta: {soap_note.get('patient_name', '—')}\n"
        f"Randevu: {soap_note.get('appointment_summary', soap_note.get('appointment_id', '—'))}\n"
        f"Seviye: {risk['level'].upper()}\n\n"
        f"Tespit edilen alanlar:\n{snippets_text}\n\n"
        f"Lütfen kaydı incele ve gerekli müdahaleyi planla."
    )

    # M3 lazy import — risk_alerts.py'yi M3'ün ağır bağımlılıklarına
    # mecbur bırakmamak için. Test edilebilirlik açısından da daha temiz.
    from module3_whatsapp_communicator import send_whatsapp_message

    try:
        send_whatsapp_message(phone, message)
        logger.warning(
            "RİSK ALARMI gönderildi | hasta=%s | level=%s | alanlar=%s",
            soap_note.get("patient_name"),
            risk["level"],
            [s["path"] for s in risk["snippets"]],
        )
        return True
    except Exception as exc:
        # Gönderim başarısızsa claim'i geri al → bir sonraki SOAP
        # üretiminde tekrar denenebilsin.
        store.forget("risk_alert", dedup_key)
        logger.error("Risk alarmı gönderilemedi: %s", exc)
        raise


def evaluate_and_alert(soap_note: dict) -> dict:
    """
    detect_risk + send_risk_alert birleşik kullanım. main.py'nin
    _audio_inbox_loop'undan tek satırla çağrılır.
    """
    risk = detect_risk(soap_note)
    sent = False
    if risk["level"] != "none":
        sent = send_risk_alert(soap_note, risk)
    return {"level": risk["level"], "alert_sent": sent, "snippets": risk["snippets"]}
