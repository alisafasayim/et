"""logging_setup.PIIRedactionFilter testleri."""

import logging

import pytest

from logging_setup import PIIRedactionFilter, redact_pii


@pytest.mark.parametrize(
    "raw",
    [
        "905321234567",
        "+90 532 123 45 67",
        "0532 123 45 67",
        "+90-532-123-4567",
    ],
)
def test_redact_pii_masks_phone_numbers(raw):
    redacted = redact_pii(f"Mesaj gönderildi: {raw}")
    assert "Mesaj gönderildi:" in redacted
    # Numaranın orta kısmı yıldız ile maskelenmeli
    assert "*" in redacted
    # 9 hanesi peş peşe görünmemeli
    import re
    assert not re.search(r"\d{8,}", redacted)


def test_redact_pii_masks_tckn():
    redacted = redact_pii("TCKN: 12345678901")
    assert "12345678901" not in redacted
    assert "*" in redacted


def test_redact_pii_masks_vkn():
    redacted = redact_pii("VKN: 1234567890")
    assert "1234567890" not in redacted


def test_filter_redacts_msg_and_args():
    f = PIIRedactionFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="Hasta %s, telefon %s",
        args=("Ali Yılmaz", "905321234567"),
        exc_info=None,
    )
    assert f.filter(record) is True
    assert "905321234567" not in record.getMessage()


def test_redact_pii_handles_empty_string():
    assert redact_pii("") == ""
    assert redact_pii(None) is None  # Defansif
