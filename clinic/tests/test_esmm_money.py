"""
M4 e-SMM para birimi & vergi davranışı testleri.

Mali doğruluk açısından KRİTİK:
- Decimal yuvarlama (kuruş hassasiyeti)
- Vergi oranları .env'den okunuyor mu
- CollectionRecord __post_init__ float girdiyi doğru Decimal'a çevirir
"""

import importlib
from decimal import Decimal

import pytest


def _reload_m4():
    """Env değişiklikleri sonrası modülü yeniden yükle."""
    import module4_esmm_generator as m4
    importlib.reload(m4)
    return m4


def test_to_money_quantizes_to_two_decimals(monkeypatch):
    m4 = _reload_m4()
    assert m4._to_money(1500) == Decimal("1500.00")
    assert m4._to_money("1500.5") == Decimal("1500.50")
    # Bankacı yuvarlaması değil ROUND_HALF_UP — 0.005 → 0.01
    assert m4._to_money("1500.005") == Decimal("1500.01")
    # Float girişi precision kaybı olmadan handle eder
    assert m4._to_money(0.1 + 0.2) == Decimal("0.30")


def test_to_money_handles_decimal_input():
    m4 = _reload_m4()
    assert m4._to_money(Decimal("99.999")) == Decimal("100.00")


def test_collection_record_normalizes_amount_in_post_init():
    m4 = _reload_m4()
    record = m4.CollectionRecord(
        patient_name="Ahmet",
        guardian_phone="05321234567",
        tax_id="12345678901",
        amount=1500.5,
        description="Muayene",
        appointment_date="2026-04-30",
    )
    assert record.amount == Decimal("1500.50")


def test_default_tax_rates_when_env_missing(monkeypatch):
    """Env'de hiç değer yokken Türkiye varsayılanlarına düşer."""
    monkeypatch.delenv("VAT_RATE", raising=False)
    monkeypatch.delenv("WITHHOLDING_RATE", raising=False)
    monkeypatch.delenv("VAT_WITHHOLDING_RATE", raising=False)
    m4 = _reload_m4()
    assert m4.VAT_RATE == Decimal("0")
    # GVK m.94 → SMK için %20 stopaj
    assert m4.WITHHOLDING_RATE == Decimal("20")
    assert m4.VAT_WITHHOLDING_RATE == Decimal("0")


def test_tax_rates_read_from_env(monkeypatch):
    monkeypatch.setenv("VAT_RATE", "10")
    monkeypatch.setenv("WITHHOLDING_RATE", "20")
    monkeypatch.setenv("VAT_WITHHOLDING_RATE", "9")
    m4 = _reload_m4()
    assert m4.VAT_RATE == Decimal("10")
    assert m4.WITHHOLDING_RATE == Decimal("20")
    assert m4.VAT_WITHHOLDING_RATE == Decimal("9")


def test_tax_rates_accept_comma_separator(monkeypatch):
    """Türkçe locale alışkanlığıyla virgüllü değer girilse de çalışsın."""
    monkeypatch.setenv("VAT_RATE", "8,5")
    m4 = _reload_m4()
    assert m4.VAT_RATE == Decimal("8.5")
