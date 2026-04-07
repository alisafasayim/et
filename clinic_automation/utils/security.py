"""
Güvenlik modülü - KVKK/HIPAA uyumlu veri şifreleme ve denetim kaydı.

Doküman: psikiyatri_dijital_asistan_teknik_mimari.md

Veri Sınıflandırma:
- KRİTİK: TC kimlik, psikiyatrik notlar -> AES-256 zorunlu
- HASSAS: İletişim bilgileri, doğum tarihi -> şifreleme önerilir
- NORMAL: Randevu saatleri, anonimleştirilmiş istatistik

Şifreleme: RSA-4096 (anahtar sarmalama) + AES-256 (veri şifreleme)
Denetim: 7 yıl immutable log, SHA-256 checksum
Saklama: Hasta dosyası 15 yıl, tıbbi kayıt 20 yıl, denetim 7 yıl
"""

import os
import json
import logging
import hashlib
import base64
from datetime import datetime
from pathlib import Path
from enum import Enum
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)


class DataClassification(Enum):
    """Veri sınıflandırma seviyeleri."""
    CRITICAL = "critical"   # TC kimlik, psikiyatrik notlar, transkriptler
    SENSITIVE = "sensitive"  # İletişim, doğum tarihi, adres
    NORMAL = "normal"       # Randevu saati, anonim istatistik


# Alan -> sınıflandırma eşlemesi
FIELD_CLASSIFICATIONS: dict[str, DataClassification] = {
    "tc_kimlik": DataClassification.CRITICAL,
    "transcript": DataClassification.CRITICAL,
    "clinical_note": DataClassification.CRITICAL,
    "diagnosis": DataClassification.CRITICAL,
    "medication": DataClassification.CRITICAL,
    "risk_assessment": DataClassification.CRITICAL,
    "phone": DataClassification.SENSITIVE,
    "email": DataClassification.SENSITIVE,
    "address": DataClassification.SENSITIVE,
    "birth_date": DataClassification.SENSITIVE,
    "parent_name": DataClassification.SENSITIVE,
    "appointment_time": DataClassification.NORMAL,
    "patient_name": DataClassification.SENSITIVE,
}


class EncryptionManager:
    """Çift katmanlı şifreleme: Fernet (hızlı) + RSA-4096/AES-256 (alan seviyesi)."""

    def __init__(self, key_path: str, rsa_key_path: str = ""):
        self.key_path = Path(key_path)
        self.rsa_key_path = Path(rsa_key_path) if rsa_key_path else None
        self.fernet = Fernet(self._load_or_create_key())
        self._rsa_private_key = None

    def _load_or_create_key(self) -> bytes:
        if self.key_path.exists():
            return self.key_path.read_bytes().strip()
        key = Fernet.generate_key()
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.write_bytes(key)
        os.chmod(str(self.key_path), 0o600)
        logger.info("Yeni şifreleme anahtarı oluşturuldu: %s", self.key_path)
        return key

    # ─── Fernet (genel amaçlı, hızlı) ───

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

    # ─── RSA-4096 + AES-256 (alan seviyesi, kritik veriler) ───

    @property
    def rsa_private_key(self):
        if self._rsa_private_key is None:
            self._rsa_private_key = self._load_or_create_rsa_key()
        return self._rsa_private_key

    def _load_or_create_rsa_key(self):
        if self.rsa_key_path and self.rsa_key_path.exists():
            pem = self.rsa_key_path.read_bytes()
            return serialization.load_pem_private_key(pem, password=None)

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,
        )

        if self.rsa_key_path:
            pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            self.rsa_key_path.parent.mkdir(parents=True, exist_ok=True)
            self.rsa_key_path.write_bytes(pem)
            os.chmod(str(self.rsa_key_path), 0o600)
            logger.info("RSA-4096 anahtarı oluşturuldu: %s", self.rsa_key_path)

        return private_key

    def encrypt_field(self, data: str) -> str:
        """Alan seviyesi şifreleme: RSA ile sarmalanmış AES-256.

        Format: base64(RSA_encrypted_AES_key + IV + AES_encrypted_data)
        """
        # Rastgele AES anahtarı ve IV oluştur
        aes_key = os.urandom(32)  # AES-256
        iv = os.urandom(16)

        # Veriyi AES-256-CBC ile şifrele
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
        encryptor = cipher.encryptor()

        # PKCS7 padding
        block_size = 16
        data_bytes = data.encode("utf-8")
        pad_len = block_size - (len(data_bytes) % block_size)
        padded = data_bytes + bytes([pad_len] * pad_len)

        encrypted_data = encryptor.update(padded) + encryptor.finalize()

        # AES anahtarını RSA ile şifrele
        public_key = self.rsa_private_key.public_key()
        encrypted_aes_key = public_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

        # Birleştir ve base64 encode
        combined = (
            len(encrypted_aes_key).to_bytes(4, "big") +
            encrypted_aes_key +
            iv +
            encrypted_data
        )
        return base64.b64encode(combined).decode("ascii")

    def decrypt_field(self, encoded: str) -> str:
        """Alan seviyesi şifre çözme."""
        combined = base64.b64decode(encoded)

        # Parse
        key_len = int.from_bytes(combined[:4], "big")
        encrypted_aes_key = combined[4:4 + key_len]
        iv = combined[4 + key_len:4 + key_len + 16]
        encrypted_data = combined[4 + key_len + 16:]

        # AES anahtarını RSA ile çöz
        aes_key = self.rsa_private_key.decrypt(
            encrypted_aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

        # Veriyi AES ile çöz
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(encrypted_data) + decryptor.finalize()

        # PKCS7 unpadding
        pad_len = padded[-1]
        return padded[:-pad_len].decode("utf-8")

    def encrypt_dict_fields(self, data: dict, field_names: list[str] | None = None) -> dict:
        """Sözlükteki hassas alanları sınıflandırmaya göre şifreler."""
        result = dict(data)
        for key, value in result.items():
            if not isinstance(value, str) or not value:
                continue

            classification = FIELD_CLASSIFICATIONS.get(key)
            if field_names and key not in field_names:
                continue

            if classification == DataClassification.CRITICAL:
                result[key] = f"ENC:RSA:{self.encrypt_field(value)}"
            elif classification == DataClassification.SENSITIVE:
                result[key] = f"ENC:FER:{self.encrypt(value).decode('ascii')}"

        return result

    def decrypt_dict_fields(self, data: dict) -> dict:
        """Şifrelenmiş alanları çözer."""
        result = dict(data)
        for key, value in result.items():
            if not isinstance(value, str):
                continue
            if value.startswith("ENC:RSA:"):
                result[key] = self.decrypt_field(value[8:])
            elif value.startswith("ENC:FER:"):
                result[key] = self.decrypt(value[8:].encode("ascii"))
        return result


class AuditLogger:
    """KVKK uyumlu denetim kaydı tutucu.

    7 yıl immutable log, SHA-256 checksum zinciri.
    """

    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_checksum = self._get_last_checksum()

    def _get_last_checksum(self) -> str:
        """Son log girdisinin checksum'ını alır (zincir bütünlüğü)."""
        if not self.log_path.exists():
            return "0" * 64
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size == 0:
                    return "0" * 64
                # Son satırı oku
                f.seek(max(0, size - 4096))
                lines = f.read().decode("utf-8", errors="replace").strip().split("\n")
                last_line = lines[-1]
                entry = json.loads(last_line)
                return entry.get("checksum", "0" * 64)
        except (json.JSONDecodeError, IndexError, KeyError):
            return "0" * 64

    def log(self, action: str, user: str, resource: str, details: str = "",
            classification: DataClassification = DataClassification.NORMAL):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "user": user,
            "resource": resource,
            "details": details,
            "classification": classification.value,
            "prev_checksum": self._last_checksum,
            "checksum": "",
        }
        # Zincirlenmiş checksum
        entry["checksum"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True).encode()
        ).hexdigest()
        self._last_checksum = entry["checksum"]

        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def log_data_access(self, patient_id: str, data_type: str, action: str = "READ"):
        classification = FIELD_CLASSIFICATIONS.get(data_type, DataClassification.NORMAL)
        self.log(
            action=f"DATA_{action}",
            user="system",
            resource=f"patient:{patient_id}",
            details=f"Veri türü: {data_type}",
            classification=classification,
        )

    def log_api_call(self, service: str, endpoint: str, status: str):
        self.log(
            action="API_CALL",
            user="system",
            resource=service,
            details=f"{endpoint} -> {status}",
        )

    def log_consent(self, patient_id: str, consent_type: str, granted: bool):
        """Onam kaydı (KVKK zorunlu)."""
        self.log(
            action="CONSENT",
            user="patient",
            resource=f"patient:{patient_id}",
            details=f"{consent_type}: {'VERİLDİ' if granted else 'REDDEDİLDİ'}",
            classification=DataClassification.CRITICAL,
        )

    def verify_chain_integrity(self) -> tuple[bool, int]:
        """Log zinciri bütünlüğünü doğrular.

        Returns:
            (geçerli_mi, doğrulanan_kayıt_sayısı)
        """
        if not self.log_path.exists():
            return True, 0

        prev_checksum = "0" * 64
        verified = 0

        with open(self.log_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    stored_checksum = entry.get("checksum", "")

                    # Beklenen prev_checksum kontrol
                    if entry.get("prev_checksum") != prev_checksum:
                        logger.error("Zincir bütünlüğü bozuk: satır %d", line_num)
                        return False, verified

                    # Checksum doğrula
                    verify_entry = dict(entry)
                    verify_entry["checksum"] = ""
                    expected = hashlib.sha256(
                        json.dumps(verify_entry, sort_keys=True).encode()
                    ).hexdigest()

                    if stored_checksum != expected:
                        logger.error("Checksum uyuşmazlığı: satır %d", line_num)
                        return False, verified

                    prev_checksum = stored_checksum
                    verified += 1
                except json.JSONDecodeError:
                    logger.error("Geçersiz JSON: satır %d", line_num)
                    return False, verified

        logger.info("Denetim kaydı bütünlüğü doğrulandı: %d kayıt", verified)
        return True, verified
