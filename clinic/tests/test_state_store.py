"""StateStore idempotency davranışı testleri."""

from pathlib import Path

import pytest

from state_store import StateStore, file_sha256


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "test.db")


def test_is_seen_false_for_unknown_key(store):
    assert store.is_seen("ns", "k1") is False


def test_mark_seen_returns_true_first_time(store):
    assert store.mark_seen("ns", "k1") is True
    assert store.is_seen("ns", "k1") is True


def test_mark_seen_returns_false_on_duplicate(store):
    store.mark_seen("ns", "k1")
    assert store.mark_seen("ns", "k1") is False


def test_claim_is_atomic(store):
    """Aynı key'i iki kez claim → sadece biri True döner."""
    assert store.claim("esmm", "tx-1") is True
    assert store.claim("esmm", "tx-1") is False


def test_namespaces_are_isolated(store):
    store.mark_seen("ns_a", "shared_key")
    assert store.is_seen("ns_b", "shared_key") is False


def test_forget_allows_reprocessing(store):
    store.mark_seen("ns", "k")
    store.forget("ns", "k")
    assert store.is_seen("ns", "k") is False
    assert store.mark_seen("ns", "k") is True


def test_meta_is_persisted(store, tmp_path):
    store.mark_seen("ns", "k", meta="ek bilgi")
    # Yeni bir bağlantıyla aç → kalıcılığı doğrula
    store.close()
    fresh = StateStore(tmp_path / "test.db")
    assert fresh.is_seen("ns", "k") is True


def test_file_sha256_is_stable(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello world")
    h1 = file_sha256(f)
    h2 = file_sha256(f)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex
