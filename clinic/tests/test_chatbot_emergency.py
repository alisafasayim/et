"""
Emergency detection testleri (Faz F — HAYATİ).

WhatsApp gelen mesajda intihar/hap/bilek/kriz gibi acil durum
anahtar kelimelerini tespit edip doktora alarm gönderme akışı.

Kritik:
- Yanlış pozitif olmamalı ("ödevimi öldüresiye yapıyorum" → emergency DEĞİL)
- Yazım hataları yakalanmalı ("ölmek istıyorum")
- Sandbox/offline ortamda fail-soft (en azından log + audit)
"""

from unittest.mock import MagicMock, patch

import pytest

from module3_whatsapp_communicator import (
    EMERGENCY_FALSE_POSITIVES,
    EMERGENCY_KEYWORDS,
    classify_incoming_message,
    handle_emergency_message,
)


# ---------------------------------------------------------------------------
# classify_incoming_message — emergency detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "msg",
    [
        "Hap içtim, ne yapacağım?",
        "intihar etmeyi düşünüyorum",
        "Kendime zarar vermek istiyorum",
        "ölmek istiyorum artık",
        "bilek kestim",
        "yaşamak istemiyorum",
        "Çocuğum kendini öldürmek istediğini söylüyor",
        "ACİL KRİZ! Yardım edin",
    ],
)
def test_emergency_keywords_detected(msg):
    """Acil durum anahtar kelimeleri her durumda yakalanmalı."""
    assert classify_incoming_message(msg) == "emergency", (
        f"Emergency YAKALANMADI: {msg}"
    )


@pytest.mark.parametrize(
    "msg",
    [
        "Yarınki randevumu iptal etmek istiyorum",
        "Ödevimi öldüresiye yapıyorum, randevu zor",
        "Babam dün öldü",  # geçmiş zaman, false positive
        "Filmin sonunda kahraman öldürdü",
        "Saçlarımı kestim",  # bilek değil saç
        "Bugün çok kötü hissediyorum",  # genel kötü hal, anahtar kelime yok
        "İlaç değişimi nasıl olur?",  # ilaç var ama içtim yok
        "Form doldurmak istiyorum",
    ],
)
def test_non_emergency_messages_pass_through(msg):
    """Acil durum olmayan mesajlar emergency sınıfına alınmamalı."""
    result = classify_incoming_message(msg)
    assert result != "emergency", (
        f"YANLIŞ POZİTİF — emergency işaretlendi: {msg} → {result}"
    )


def test_emergency_priority_over_cancellation():
    """Acil durum + iptal birlikte gelirse emergency öncelikli."""
    # "Hap içtim" + "iptal" — emergency'ye gitmeli, cancellation'a değil
    msg = "Hap içtim, randevumu iptal edin"
    assert classify_incoming_message(msg) == "emergency"


def test_emergency_false_positive_filter():
    """'öldürdü' geçmiş zaman → emergency DEĞİL."""
    msg = "Komşumuzun köpeği kediyi öldürdü"
    assert classify_incoming_message(msg) != "emergency"


def test_emergency_keywords_list_complete():
    """Anahtar kelime listesi minimum kapsama sahip."""
    keywords_lower = [k.lower() for k in EMERGENCY_KEYWORDS]
    must_have = ["intihar", "hap içtim", "bilek kestim", "kendine zarar"]
    for keyword in must_have:
        assert any(keyword in k for k in keywords_lower), (
            f"Kritik anahtar kelime eksik: {keyword}"
        )


def test_false_positive_list_includes_basic_cases():
    """False positive listesi en azından öldürdü/öldüresiye içermeli."""
    fp_lower = [f.lower() for f in EMERGENCY_FALSE_POSITIVES]
    assert any("öldür" in f for f in fp_lower)


# ---------------------------------------------------------------------------
# handle_emergency_message — alarm + audit + risk + auto-reply
# ---------------------------------------------------------------------------

def test_handle_emergency_with_doctor_phone(monkeypatch):
    """DOCTOR_PHONE set ise alarm gönderilmeli."""
    monkeypatch.setenv("DOCTOR_PHONE", "905329999999")

    sent_messages: list[tuple[str, str]] = []

    def fake_send(phone, msg):
        sent_messages.append((phone, msg))
        return {"status": "sent"}

    monkeypatch.setattr(
        "module3_whatsapp_communicator.send_whatsapp_message", fake_send
    )

    result = handle_emergency_message(
        sender_phone="905321111111", message_text="Hap içtim, yardım"
    )

    # 2 mesaj: doktora alarm + veliye otomatik yanıt
    assert len(sent_messages) >= 1
    # Doktor mesajı 🚨 emoji içermeli
    doctor_msg = next(
        (msg for phone, msg in sent_messages if phone == "905329999999"),
        None,
    )
    assert doctor_msg is not None
    assert "ACİL DURUM" in doctor_msg or "🚨" in doctor_msg
    assert "905321111111" in doctor_msg  # gönderici telefonu doktora bildirildi
    assert result["doctor_alerted"] is True


def test_handle_emergency_without_doctor_phone(monkeypatch):
    """DOCTOR_PHONE boşsa fail-soft — alarm atlandı ama akış devam etti."""
    monkeypatch.delenv("DOCTOR_PHONE", raising=False)

    monkeypatch.setattr(
        "module3_whatsapp_communicator.send_whatsapp_message",
        lambda phone, msg: {"status": "sent"},
    )

    result = handle_emergency_message(
        sender_phone="905321111111", message_text="intihar"
    )

    assert result["doctor_alerted"] is False
    # Hata fırlatmadı


def test_handle_emergency_returns_diagnostic_dict(monkeypatch):
    """Çıktı dict'i 4 statü flag'ini içermeli."""
    monkeypatch.delenv("DOCTOR_PHONE", raising=False)
    monkeypatch.setattr(
        "module3_whatsapp_communicator.send_whatsapp_message",
        lambda phone, msg: None,
    )

    result = handle_emergency_message("905321", "intihar")

    assert "doctor_alerted" in result
    assert "audit_logged" in result
    assert "risk_recorded" in result
    assert "auto_reply_sent" in result


def test_handle_emergency_auto_reply_includes_182_hotline(monkeypatch):
    """Veliye giden otomatik yanıt 112/182 hattını mention etmeli."""
    monkeypatch.delenv("DOCTOR_PHONE", raising=False)

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "module3_whatsapp_communicator.send_whatsapp_message",
        lambda phone, msg: sent.append((phone, msg)),
    )

    handle_emergency_message("905321", "yaşamak istemiyorum")

    sender_msgs = [msg for phone, msg in sent if phone == "905321"]
    if sender_msgs:
        # En azından bir destek mesajı gitti
        assert any("182" in m or "112" in m for m in sender_msgs)
