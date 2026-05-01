"""
Yerel Hasta Kayıt Deposu (Türkiye'de tutulan PII).

Notion'a (ABD) gönderilen hiçbir veri kişiyi tanımlayan bilgi içermez —
sadece UUID + kısa pseudonym (#a4f9-c2b1). Tanımlayıcı ↔ kişi
eşleşmesi YALNIZCA bu modüldeki yerel SQLite tablosunda yaşar.

KVKK m.9 (yurt dışı veri aktarımı) bu mimari ile tetiklenmez:
Notion'a giden veri "anonim hale getirilmiş" sayılır, çünkü tek
başına kişiyi belirleyemez (anahtar yalnızca yerel tabloda).

Tablolar:
  patients
    uuid           PRIMARY KEY  (UUID v4 string)
    full_name_enc  Fernet ile şifreli ad-soyad
    tax_id_enc     Fernet ile şifreli TCKN
    phone_enc      Fernet ile şifreli telefon
    birth_date     ISO 'YYYY-MM-DD' (yaş için bilgi; 11 yaş gibi
                   düşük granülerlikte de tutulabilir, opsiyonel)
    tax_id_hash    HMAC-SHA256 (PII_HASH_KEY) — TCKN'ye göre arama
    full_name_hash HMAC-SHA256 — isim arama (basit; tam eşleşme)
    notion_page_id Notion kök sayfası ID'si (hierarchical mode)
    consent_at     Açık rıza alınma zamanı (ISO datetime); boşsa
                   "henüz onam yok" → kayıt sadece referans amaçlı
    created_at
    updated_at
"""

import logging
import os
import sqlite3
import threading
import uuid as uuid_lib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from pii_crypto import decrypt, encrypt, pseudo_hash, short_pseudonym

logger = logging.getLogger("patient_registry")

def _default_db_path() -> Path:
    """Env her seferinde okunur — test'lerde fixture sırası önemli olmasın."""
    return Path(os.getenv("PATIENT_REGISTRY_DB", "./patient_registry.db"))


class PatientRegistry:
    """Thread-safe SQLite hasta deposu."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            db_path = _default_db_path()
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
                CREATE TABLE IF NOT EXISTS patients (
                    uuid            TEXT PRIMARY KEY,
                    full_name_enc   TEXT NOT NULL,
                    tax_id_enc      TEXT,
                    phone_enc       TEXT,
                    birth_date      TEXT,
                    tax_id_hash     TEXT,
                    full_name_hash  TEXT,
                    notion_page_id  TEXT,
                    consent_at      TEXT,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_patients_tax_id_hash "
                "ON patients(tax_id_hash)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_patients_full_name_hash "
                "ON patients(full_name_hash)"
            )

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    # ----- public API -----

    def create_patient(
        self,
        full_name: str,
        tax_id: str = "",
        phone: str = "",
        birth_date: str = "",
        consent_at: Optional[str] = None,
    ) -> str:
        """
        Yeni hasta kaydı. Döner: UUID.
        Aynı tax_id zaten kayıtlıysa onun UUID'sini döner (tekilleştirme).
        """
        if not full_name:
            raise ValueError("full_name zorunlu")

        # Tekilleştirme: TCKN varsa hash'e bak
        if tax_id:
            existing = self.find_by_tax_id(tax_id)
            if existing:
                logger.info("Mevcut hasta UUID döndürüldü (tax_id eşleşmesi)")
                return existing["uuid"]

        new_uuid = str(uuid_lib.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO patients (
                    uuid, full_name_enc, tax_id_enc, phone_enc, birth_date,
                    tax_id_hash, full_name_hash, consent_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_uuid,
                    encrypt(full_name),
                    encrypt(tax_id),
                    encrypt(phone),
                    birth_date,
                    pseudo_hash(tax_id),
                    pseudo_hash(full_name.lower().strip()),
                    consent_at,
                    now,
                    now,
                ),
            )
        logger.info("Yeni hasta kaydı: %s", short_pseudonym(new_uuid))
        try:
            from audit_log import audit
            audit("patient.create", patient_uuid=new_uuid, details={"has_tax_id": bool(tax_id)})
        except Exception:
            pass  # audit yazılamadı; ana akış devam etsin (logger zaten yazdı)
        return new_uuid

    def get_patient(self, patient_uuid: str) -> Optional[dict]:
        """UUID ile hasta kaydını çözer (decrypt eder)."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM patients WHERE uuid=?", (patient_uuid,)
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
        rec = dict(zip(cols, row))
        return self._decrypt_record(rec)

    def find_by_tax_id(self, tax_id: str) -> Optional[dict]:
        """TCKN/VKN üzerinden tek-tek arama. Hash bazlı, sabit zamanlı."""
        if not tax_id:
            return None
        h = pseudo_hash(tax_id)
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM patients WHERE tax_id_hash=? LIMIT 1", (h,)
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
        return self._decrypt_record(dict(zip(cols, row)))

    def find_by_name(self, full_name: str) -> list[dict]:
        """
        Tam eşleşmeli isim araması (case-insensitive, trim).
        Birden fazla aynı isimli olabilir (homonim) → liste döner.
        """
        if not full_name:
            return []
        h = pseudo_hash(full_name.lower().strip())
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM patients WHERE full_name_hash=?", (h,)
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [self._decrypt_record(dict(zip(cols, r))) for r in rows]

    def attach_notion_page(self, patient_uuid: str, notion_page_id: str) -> None:
        """Hasta için Notion kök sayfası ID'sini kaydeder."""
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._cursor() as cur:
            cur.execute(
                "UPDATE patients SET notion_page_id=?, updated_at=? WHERE uuid=?",
                (notion_page_id, now, patient_uuid),
            )

    def record_consent(self, patient_uuid: str, when: Optional[str] = None) -> None:
        """Açık rıza alınma zamanını işler (KVKK m.5/2)."""
        when = when or datetime.now(tz=timezone.utc).isoformat()
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._cursor() as cur:
            cur.execute(
                "UPDATE patients SET consent_at=?, updated_at=? WHERE uuid=?",
                (when, now, patient_uuid),
            )
        try:
            from audit_log import audit
            audit("consent.grant", patient_uuid=patient_uuid, details={"at": when})
        except Exception:
            pass

    def revoke_consent(self, patient_uuid: str) -> None:
        """Hasta/veli rızasını geri çekti — kayıt belirlenir, ileride
        delete'e geçer (KVKK m.7 unutulma hakkı)."""
        with self._cursor() as cur:
            cur.execute(
                "UPDATE patients SET consent_at=NULL, updated_at=? WHERE uuid=?",
                (datetime.now(tz=timezone.utc).isoformat(), patient_uuid),
            )
        try:
            from audit_log import audit
            audit("consent.revoke", patient_uuid=patient_uuid)
        except Exception:
            pass

    def delete_patient(self, patient_uuid: str) -> bool:
        """
        Hasta kaydını fiziksel siler (KVKK m.7 unutulma hakkı).
        Notion sayfası ayrı silinmelidir; bu fonksiyon sadece yerel
        deposu temizler.
        """
        with self._cursor() as cur:
            cur.execute("DELETE FROM patients WHERE uuid=?", (patient_uuid,))
            deleted = cur.rowcount > 0
        if deleted:
            try:
                from audit_log import audit
                audit("patient.delete", patient_uuid=patient_uuid)
            except Exception:
                pass
        return deleted

    def list_all(self, limit: int = 100) -> list[dict]:
        """Tüm hastaları döner (admin paneli için)."""
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM patients ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [self._decrypt_record(dict(zip(cols, r))) for r in rows]

    # ----- helpers -----

    @staticmethod
    def _decrypt_record(rec: dict) -> dict:
        rec["full_name"] = decrypt(rec.get("full_name_enc", "") or "")
        rec["tax_id"] = decrypt(rec.get("tax_id_enc", "") or "")
        rec["phone"] = decrypt(rec.get("phone_enc", "") or "")
        rec["pseudonym"] = short_pseudonym(rec["uuid"])
        return rec

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_default: Optional[PatientRegistry] = None


def get_default_registry() -> PatientRegistry:
    global _default
    if _default is None:
        _default = PatientRegistry()
        logger.info("PatientRegistry başlatıldı: %s", _default.db_path)
    return _default


def reset_cache() -> None:
    """Test'ler için."""
    global _default
    if _default is not None:
        _default.close()
    _default = None
