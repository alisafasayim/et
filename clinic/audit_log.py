"""
KVKK m.12 — Veri Erişim Audit Log.

Hasta verisine yapılan her okuma/yazma kaydı. Append-only (UPDATE/DELETE
yok). Denetim ve veri ihlali olayında "kim ne yaptı" zincirini ortaya
koyar.

Şema:
    audit_events
      id          INTEGER PRIMARY KEY (autoincrement)
      ts          ISO datetime UTC
      actor       'system' | 'admin:<user>' | 'webhook:<source>'
      action      'patient.create' | 'patient.read' | 'patient.update'
                  'patient.delete' | 'consent.grant' | 'consent.revoke'
                  'soap.archive' | 'esmm.issue' | 'reminder.send'
                  'risk.alert' | 'admin.access' | ...
      patient_uuid TEXT NULL  (varsa hangi hasta)
      details     JSON / serbest metin (PII içermemeli)

İlke: details alanına PII (TCKN, telefon, ad) YAZMA — pseudonym ve
event_id gibi opaque tanımlayıcılar kullan.
"""

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger("audit_log")


def _default_db_path() -> Path:
    return Path(os.getenv("AUDIT_LOG_DB", "./audit_log.db"))


class AuditLog:
    """Thread-safe append-only audit log."""

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
                CREATE TABLE IF NOT EXISTS audit_events (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts           TEXT NOT NULL,
                    actor        TEXT NOT NULL,
                    action       TEXT NOT NULL,
                    patient_uuid TEXT,
                    details      TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_ts "
                "ON audit_events(ts)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_patient "
                "ON audit_events(patient_uuid)"
            )

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def record(
        self,
        action: str,
        actor: str = "system",
        patient_uuid: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> int:
        """
        Yeni audit event kaydı. Döner: kayıt ID'si.
        details: PII içermemeli — sadece metadata (event_id, status, vb.)
        """
        ts = datetime.now(tz=timezone.utc).isoformat()
        details_json = json.dumps(details, ensure_ascii=False) if details else None
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_events (ts, actor, action, patient_uuid, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, actor, action, patient_uuid, details_json),
            )
            return cur.lastrowid

    def query(
        self,
        patient_uuid: Optional[str] = None,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict]:
        """
        Audit kayıtlarını filtreler. Tüm parametreler opsiyonel; verilmezse
        son `limit` kaydı döner.
        """
        clauses = []
        params: list[Any] = []
        if patient_uuid:
            clauses.append("patient_uuid = ?")
            params.append(patient_uuid)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if since:
            clauses.append("ts >= ?")
            params.append(since)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT id, ts, actor, action, patient_uuid, details "
            f"FROM audit_events{where} ORDER BY id DESC LIMIT ?"
        )
        params.append(limit)

        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        out = []
        for row in rows:
            details = None
            if row[5]:
                try:
                    details = json.loads(row[5])
                except json.JSONDecodeError:
                    details = row[5]
            out.append(
                {
                    "id": row[0],
                    "ts": row[1],
                    "actor": row[2],
                    "action": row[3],
                    "patient_uuid": row[4],
                    "details": details,
                }
            )
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_default: Optional[AuditLog] = None


def get_default_audit_log() -> AuditLog:
    global _default
    if _default is None:
        _default = AuditLog()
        logger.info("AuditLog başlatıldı: %s", _default.db_path)
    return _default


def reset_cache() -> None:
    global _default
    if _default is not None:
        _default.close()
    _default = None


# ---------------------------------------------------------------------------
# Convenience wrapper'lar — modüllerin kısa import için kullanması için
# ---------------------------------------------------------------------------

def audit(
    action: str,
    actor: str = "system",
    patient_uuid: Optional[str] = None,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """get_default_audit_log().record()'un kısa alias'ı.
    Hata durumunda log'lar ama yine de raise eder — audit kaydı kaybı
    sessizce gizlenmemeli (KVKK denetiminde sorun)."""
    try:
        get_default_audit_log().record(action, actor, patient_uuid, details)
    except Exception as exc:
        logger.error("Audit log yazımı başarısız: %s | %s/%s", exc, actor, action)
        raise
