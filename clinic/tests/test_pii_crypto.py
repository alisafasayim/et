"""pii_crypto: şifreleme, çözme, pseudo hash."""

import pytest

cryptography = pytest.importorskip("cryptography", reason="cryptography yüklü değil")
from cryptography.fernet import Fernet, InvalidToken


@pytest.fixture
def fresh_keys(monkeypatch):
    """Her test için yeni Fernet ve hash anahtarı."""
    monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PII_HASH_KEY", "test-hash-salt")
    import pii_crypto
    pii_crypto.reset_cache()
    return pii_crypto


def test_encrypt_decrypt_roundtrip(fresh_keys):
    plain = "12345678901"
    cipher = fresh_keys.encrypt(plain)
    assert cipher != plain
    assert fresh_keys.decrypt(cipher) == plain


def test_encrypt_empty_returns_empty(fresh_keys):
    assert fresh_keys.encrypt("") == ""
    assert fresh_keys.decrypt("") == ""


def test_encrypt_different_each_time(fresh_keys):
    """Fernet IV randomize → aynı plaintext farklı ciphertext üretir."""
    a = fresh_keys.encrypt("12345")
    b = fresh_keys.encrypt("12345")
    assert a != b
    # Yine de ikisi de aynı plaintext'e çözülür
    assert fresh_keys.decrypt(a) == fresh_keys.decrypt(b) == "12345"


def test_decrypt_with_wrong_key_raises(fresh_keys, monkeypatch):
    cipher = fresh_keys.encrypt("12345")
    # Anahtarı değiştir
    monkeypatch.setenv("PII_ENCRYPTION_KEY", Fernet.generate_key().decode())
    fresh_keys.reset_cache()
    with pytest.raises(InvalidToken):
        fresh_keys.decrypt(cipher)


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
    import pii_crypto
    pii_crypto.reset_cache()
    with pytest.raises(EnvironmentError):
        pii_crypto.encrypt("test")


def test_pseudo_hash_deterministic(fresh_keys):
    a = fresh_keys.pseudo_hash("12345678901")
    b = fresh_keys.pseudo_hash("12345678901")
    assert a == b
    assert len(a) == 64  # HMAC-SHA256 hex


def test_pseudo_hash_different_inputs_different_outputs(fresh_keys):
    assert fresh_keys.pseudo_hash("12345") != fresh_keys.pseudo_hash("12346")


def test_pseudo_hash_different_salts_different_outputs(monkeypatch):
    monkeypatch.setenv("PII_HASH_KEY", "salt-A")
    import pii_crypto
    a = pii_crypto.pseudo_hash("12345")

    monkeypatch.setenv("PII_HASH_KEY", "salt-B")
    b = pii_crypto.pseudo_hash("12345")
    assert a != b


def test_pseudo_hash_empty(fresh_keys):
    assert fresh_keys.pseudo_hash("") == ""


def test_short_pseudonym(fresh_keys):
    assert fresh_keys.short_pseudonym("a4f9c2b1-3e8d-4a5f-9c1e-12345abc6789") == "#a4f9-c2b1"
    assert fresh_keys.short_pseudonym("") == "#unknown"
    assert fresh_keys.short_pseudonym("abc") == "#abc"
