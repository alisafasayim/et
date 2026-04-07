"""
Merkezi konfigürasyon yönetimi.
Tüm API anahtarları ve ayarlar .env dosyasından okunur.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class GoogleConfig:
    credentials_path: str = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    token_path: str = os.getenv("GOOGLE_TOKEN_PATH", "token.json")
    calendar_id: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    form_id: str = os.getenv("GOOGLE_FORM_ID", "")
    scopes: list = field(default_factory=lambda: [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/forms.responses.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ])


@dataclass
class NotionConfig:
    api_key: str = os.getenv("NOTION_API_KEY", "")
    patients_db_id: str = os.getenv("NOTION_PATIENTS_DB_ID", "")
    sessions_db_id: str = os.getenv("NOTION_SESSIONS_DB_ID", "")
    audio_records_db_id: str = os.getenv("NOTION_AUDIO_RECORDS_DB_ID", "")
    form_responses_db_id: str = os.getenv("NOTION_FORM_RESPONSES_DB_ID", "")
    staff_db_id: str = os.getenv("NOTION_STAFF_DB_ID", "")
    api_version: str = "2022-06-28"


@dataclass
class WhatsAppConfig:
    provider: str = os.getenv("WHATSAPP_PROVIDER", "twilio")  # twilio, evolution, 360dialog
    # Twilio
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_whatsapp_number: str = os.getenv("TWILIO_WHATSAPP_NUMBER", "")
    # Evolution API
    evolution_api_url: str = os.getenv("EVOLUTION_API_URL", "")
    evolution_api_key: str = os.getenv("EVOLUTION_API_KEY", "")
    evolution_instance: str = os.getenv("EVOLUTION_INSTANCE", "")
    # 360dialog
    dialog360_api_key: str = os.getenv("DIALOG360_API_KEY", "")
    dialog360_channel_id: str = os.getenv("DIALOG360_CHANNEL_ID", "")
    # Chatbot
    chatbot_enabled: bool = os.getenv("CHATBOT_ENABLED", "true").lower() == "true"
    chatbot_confidence_threshold: float = float(os.getenv("CHATBOT_CONFIDENCE_THRESHOLD", "0.85"))
    messaging_hours_start: int = int(os.getenv("MESSAGING_HOURS_START", "8"))
    messaging_hours_end: int = int(os.getenv("MESSAGING_HOURS_END", "21"))


@dataclass
class TranscriptionConfig:
    provider: str = os.getenv("TRANSCRIPTION_PROVIDER", "openai")  # openai veya local
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    whisper_model: str = os.getenv("WHISPER_MODEL", "whisper-1")
    local_model_size: str = os.getenv("WHISPER_LOCAL_MODEL", "large-v3")
    language: str = "tr"
    audio_dir: str = os.getenv("AUDIO_DIR", str(BASE_DIR / "audio_files"))


@dataclass
class LLMConfig:
    provider: str = os.getenv("LLM_PROVIDER", "anthropic")  # anthropic veya openai
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o")


@dataclass
class SecurityConfig:
    encryption_key_path: str = os.getenv("ENCRYPTION_KEY_PATH", str(BASE_DIR / ".encryption_key"))
    rsa_key_path: str = os.getenv("RSA_KEY_PATH", str(BASE_DIR / ".rsa_key.pem"))
    encrypt_audio: bool = os.getenv("ENCRYPT_AUDIO", "true").lower() == "true"
    encrypt_transcripts: bool = os.getenv("ENCRYPT_TRANSCRIPTS", "true").lower() == "true"
    field_level_encryption: bool = os.getenv("FIELD_LEVEL_ENCRYPTION", "true").lower() == "true"
    audit_log_path: str = os.getenv("AUDIT_LOG_PATH", str(BASE_DIR / "audit.log"))
    data_retention_days: int = int(os.getenv("DATA_RETENTION_DAYS", "2555"))  # KVKK: 7 yıl
    medical_retention_years: int = int(os.getenv("MEDICAL_RETENTION_YEARS", "20"))
    patient_file_retention_years: int = int(os.getenv("PATIENT_FILE_RETENTION_YEARS", "15"))


@dataclass
class AppConfig:
    google: GoogleConfig = field(default_factory=GoogleConfig)
    notion: NotionConfig = field(default_factory=NotionConfig)
    whatsapp: WhatsAppConfig = field(default_factory=WhatsAppConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    timezone: str = os.getenv("TIMEZONE", "Europe/Istanbul")
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"


def get_config() -> AppConfig:
    return AppConfig()
