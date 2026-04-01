"""
Güvenlik modülü - KVKK/HIPAA uyumlu veri şifreleme ve denetim kaydı.
"""

import os
import json
import logging
import hashlib
from datetime import datetime
from pathlib import Path
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


class EncryptionManager:
    """Fernet simetrik şifreleme ile hassas verileri korur."""

    def __init__(self, key_path: str):
        self.key_path = Path(key_path)
        self.fernet = Fernet(self._load_or_create_key())

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        key = Fernet.generate_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(key)
        os.chmod(str(self.key_path), 0o600)
        logger.info("Yeni şifreleme anahtarı oluşturuldu: %s", self.key_path)
        return key

    def encrypt(self, data: str) -> bytes:
        return self.fernet.encrypt(data.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        return self.fernet.decrypt(token).decode("utf-8")

    def encrypt_file(self, source_path: str, dest_path: str | None = None) -> str:
        dest = dest_path or f"{source_path}.enc"
        data = Path(source_path).read_bytes()
        encrypted = self.fernet.encrypt(data)
        Path(dest).write_bytes(encrypted)
        logger.info("Dosya şifrelendi: %s -> %s", source_path, dest)
        return dest

    def decrypt_file(self, source_path: str, dest_path: str | None = None) -> str:
        dest = dest_path or source_path.replace(".enc", "")
        encrypted = Path(source_path).read_bytes()
        decrypted = self.fernet.decrypt(encrypted)
        Path(dest).write_bytes(decrypted)
        return dest


class AuditLogger:
    """KVKK uyumlu denetim kaydı tutucu."""

    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, action: str, user: str, resource: str, details: str = ""):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "user": user,
            "resource": resource,
            "details": details,
            "checksum": "",
        }
        entry["checksum"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True).encode()
        ).hexdigest()

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_data_access(self, patient_id: str, data_type: str, action: str = "READ"):
        self.log(
            action=f"DATA_{action}",
            user="system",
            resource=f"patient:{patient_id}",
            details=f"Veri türü: {data_type}",
        )

    def log_api_call(self, service: str, endpoint: str, status: str):
        self.log(
            action="API_CALL",
            user="system",
            resource=service,
            details=f"{endpoint} -> {status}",
        )
