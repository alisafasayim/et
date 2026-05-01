"""
Klinik Sistemi — Hafif Idempotency / State Store

SQLite tabanlı, tek dosya, dış bağımlılığı yok. Her modül kendi
"namespace" altında işlenmiş kayıtları işaretler; aynı kaynağı
ikinci kez işlemeyi önler.

Kullanım örnekleri:
    from state_store import StateStore

    store = StateStore()  # ./clinic_state.db

    if store.is_seen("calendar_event", event_id):
        continue
    send_message(...)
    store.mark_seen("calendar_event", event_id, meta={"phone": "..."})

Namespace'ler:
    - calendar_event   → M3 WhatsApp hatırlatma gönderildi mi
    - audio_file       → M1 ses dosyası işlendi mi (sha-256 hash)
    - soap_archive     → M2 Notion'a arşivlendi mi (appointment_id)
    - esmm_invoice     → M4 e-SMM kesildi mi (collection key)
"""

import hashlib
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger("state_store")

DEFAULT_DB_PATH = Path(os.getenv("CLINIC_STATE_DB", "./clinic_state.db"))


class StateStore:
    """
    Thread-safe SQLite state store. Tek bir SQLite bağlantısı kullanır;
    SQLite'ın varsayılan thread-checking'i nedeniyle bağlantıyı tek
    thread'den kullanmak en güvenlisi — biz `check_same_thread=False`
    + module-level lock ile koruyoruz.
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.db_path, check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed (
                    namespace TEXT NOT NULL,
                    key       TEXT NOT NULL,
                    meta      TEXT,
                    seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (namespace, key)
                )
                """
            )

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    # ----- Public API -----

    def is_seen(self, namespace: str, key: str) -> bool:
        """O kaynağın daha önce işlenip işlenmediğini döner."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT 1 FROM processed WHERE namespace=? AND key=? LIMIT 1",
                (namespace, key),
            )
            return cur.fetchone() is not None

    def mark_seen(
        self,
        namespace: str,
        key: str,
        meta: Optional[str] = None,
    ) -> bool:
        """
        Kaynağı işlenmiş olarak işaretler.
        Döner: True (yeni kayıt eklendi) / False (zaten vardı).
        """
        with self._cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO processed (namespace, key, meta) VALUES (?, ?, ?)",
                    (namespace, key, meta),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def claim(
        self,
        namespace: str,
        key: str,
        meta: Optional[str] = None,
    ) -> bool:
        """
        Atomik check-and-mark. Aynı anda iki worker aynı key'i işlemeye
        kalkarsa sadece biri True alır. mark_seen ile aynı; ayrı isim
        çağıran tarafta niyetin "ben işliyorum" olduğunu belirtir.
        """
        return self.mark_seen(namespace, key, meta)

    def forget(self, namespace: str, key: str) -> None:
        """Bir kaydı geri al (manuel müdahale / yeniden işleme için)."""
        with self._cursor() as cur:
            cur.execute(
                "DELETE FROM processed WHERE namespace=? AND key=?",
                (namespace, key),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------

def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Ses dosyası gibi büyük dosyalar için streaming SHA-256."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Modül seviyesinde paylaşılan singleton
# ---------------------------------------------------------------------------

_default_store: Optional[StateStore] = None


def get_default_store() -> StateStore:
    """Tüm modüllerin paylaştığı singleton store."""
    global _default_store
    if _default_store is None:
        _default_store = StateStore()
        logger.info("StateStore başlatıldı: %s", _default_store.db_path)
    return _default_store
