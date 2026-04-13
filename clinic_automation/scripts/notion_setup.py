"""
Notion Veritabanı Otomatik Kurulum Script'i
============================================
Kliniğin ihtiyaç duyduğu 5 Notion veritabanını otomatik oluşturur.

Kullanım:
    python scripts/notion_setup.py --parent-page-id <PAGE_ID>

Adımlar:
1. Notion'da yeni bir sayfa oluşturun (ör: "Klinik Sistemi")
2. Sayfanın URL'inden ID'yi alın:
   notion.so/xxx/Klinik-Sistemi-<BURASI>?v=...
3. Bu script'i çalıştırın
4. Oluşturulan DB ID'lerini .env dosyasına yazın
"""

import sys
import argparse
import logging
from pathlib import Path

# Proje kök dizinini path'e ekle
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from notion_client import Client as NotionSDK
from clinic_automation.config.settings import get_config

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# ─── Veritabanı Şemaları ───

PATIENTS_DB_SCHEMA = {
    "name": "Hastalar",
    "properties": {
        "İsim": {"title": {}},
        "Yaş": {"number": {"format": "number"}},
        "Yaş Grubu": {"select": {"options": [
            {"name": "Okul Öncesi (3-6)", "color": "yellow"},
            {"name": "İlkokul (7-10)", "color": "green"},
            {"name": "Ortaokul (11-14)", "color": "blue"},
            {"name": "Lise (15-18)", "color": "purple"},
        ]}},
        "Veli Adı": {"rich_text": {}},
        "Telefon": {"phone_number": {}},
        "Tanı": {"rich_text": {}},
        "DSM-5 Kodu": {"rich_text": {}},
        "Durum": {"select": {"options": [
            {"name": "Aktif", "color": "green"},
            {"name": "Değerlendirmede", "color": "yellow"},
            {"name": "Takip", "color": "blue"},
            {"name": "Pasif", "color": "gray"},
        ]}},
        "Öncelik": {"select": {"options": [
            {"name": "Rutin", "color": "gray"},
            {"name": "Yakın", "color": "yellow"},
            {"name": "Acil", "color": "orange"},
            {"name": "Kriz", "color": "red"},
        ]}},
        "Risk Seviyesi": {"select": {"options": [
            {"name": "Düşük", "color": "green"},
            {"name": "Orta", "color": "yellow"},
            {"name": "Yüksek", "color": "orange"},
            {"name": "Kritik", "color": "red"},
        ]}},
        "Yolculuk Aşaması": {"select": {"options": [
            {"name": "Başvuru", "color": "gray"},
            {"name": "Triyaj", "color": "yellow"},
            {"name": "Ön Değerlendirme", "color": "blue"},
            {"name": "Klinik Değerlendirme", "color": "purple"},
            {"name": "Tanı", "color": "orange"},
            {"name": "Tedavi", "color": "green"},
            {"name": "İzlem", "color": "teal"},
            {"name": "Sonlandırma", "color": "gray"},
        ]}},
        "Başvuru Kaynağı": {"select": {"options": [
            {"name": "Aile Başvurusu", "color": "blue"},
            {"name": "Okul Yönlendirmesi", "color": "green"},
            {"name": "Hekim Yönlendirmesi", "color": "purple"},
            {"name": "Hastane", "color": "orange"},
        ]}},
        "Başlangıç Tarihi": {"date": {}},
        "Son Randevu": {"date": {}},
        "Notlar": {"rich_text": {}},
    },
}

SESSIONS_DB_SCHEMA = {
    "name": "Konsültasyonlar",
    "properties": {
        "Başlık": {"title": {}},
        "Hasta": {"relation": {"database_id": "PATIENTS_DB_ID", "single_property": {}}},
        "Tarih": {"date": {}},
        "Seans Türü": {"select": {"options": [
            {"name": "İlk Görüşme", "color": "blue"},
            {"name": "Takip", "color": "green"},
            {"name": "Aile Görüşmesi", "color": "purple"},
            {"name": "Okul Görüşmesi", "color": "orange"},
            {"name": "Kriz Müdahalesi", "color": "red"},
        ]}},
        "Süre (dk)": {"number": {"format": "number"}},
        "Tanı": {"rich_text": {}},
        "Risk Seviyesi": {"select": {"options": [
            {"name": "Düşük", "color": "green"},
            {"name": "Orta", "color": "yellow"},
            {"name": "Yüksek", "color": "orange"},
            {"name": "Kritik", "color": "red"},
        ]}},
        "Durum": {"select": {"options": [
            {"name": "Planlandı", "color": "gray"},
            {"name": "Tamamlandı", "color": "green"},
            {"name": "İptal", "color": "red"},
            {"name": "İnceleme Bekliyor", "color": "yellow"},
        ]}},
        "Not Oluşturuldu": {"checkbox": {}},
    },
}

AUDIO_DB_SCHEMA = {
    "name": "Ses Kayıtları",
    "properties": {
        "Başlık": {"title": {}},
        "Hasta": {"relation": {"database_id": "PATIENTS_DB_ID", "single_property": {}}},
        "Tarih": {"date": {}},
        "Dosya Adı": {"rich_text": {}},
        "Süre (sn)": {"number": {"format": "number"}},
        "Kalite": {"select": {"options": [
            {"name": "excellent", "color": "green"},
            {"name": "good", "color": "blue"},
            {"name": "fair", "color": "yellow"},
            {"name": "poor", "color": "red"},
        ]}},
        "Eşleşme Güveni": {"number": {"format": "percent"}},
        "Transkript Durumu": {"select": {"options": [
            {"name": "Bekliyor", "color": "gray"},
            {"name": "İşlendi", "color": "green"},
            {"name": "İnceleme Gerekli", "color": "yellow"},
            {"name": "Hata", "color": "red"},
        ]}},
    },
}

FORMS_DB_SCHEMA = {
    "name": "Form Yanıtları",
    "properties": {
        "Başlık": {"title": {}},
        "Hasta": {"relation": {"database_id": "PATIENTS_DB_ID", "single_property": {}}},
        "Form Tarihi": {"date": {}},
        "Şikayet": {"rich_text": {}},
        "Form Türü": {"select": {"options": [
            {"name": "İlk Başvuru Anamnezi", "color": "blue"},
            {"name": "Takip Formu", "color": "green"},
            {"name": "Aile Değerlendirmesi", "color": "purple"},
        ]}},
        "Tamamlandı": {"checkbox": {}},
    },
}

STAFF_DB_SCHEMA = {
    "name": "Personel",
    "properties": {
        "İsim": {"title": {}},
        "Unvan": {"select": {"options": [
            {"name": "Çocuk Psikiyatristi", "color": "blue"},
            {"name": "Psikolog", "color": "green"},
            {"name": "Sosyal Hizmet Uzmanı", "color": "purple"},
            {"name": "Hemşire", "color": "yellow"},
            {"name": "Sekreter", "color": "gray"},
        ]}},
        "Telefon": {"phone_number": {}},
        "E-posta": {"email": {}},
        "Aktif": {"checkbox": {}},
    },
}

ALL_SCHEMAS = [PATIENTS_DB_SCHEMA, SESSIONS_DB_SCHEMA, AUDIO_DB_SCHEMA, FORMS_DB_SCHEMA, STAFF_DB_SCHEMA]


class NotionSetup:
    """Notion veritabanlarını otomatik oluşturan kurulum aracı."""

    def __init__(self, api_key: str):
        self.client = NotionSDK(auth=api_key)
        self.created_dbs = {}

    def create_database(self, parent_page_id: str, schema: dict) -> str:
        """Tek bir veritabanı oluşturur."""
        # Relation'ları düzelt
        properties = {}
        for prop_name, prop_def in schema["properties"].items():
            if "relation" in prop_def and prop_def["relation"].get("database_id") == "PATIENTS_DB_ID":
                if "PATIENTS_DB" in self.created_dbs:
                    prop_def = {"relation": {
                        "database_id": self.created_dbs["PATIENTS_DB"],
                        "single_property": {},
                    }}
                else:
                    # Patients DB henüz oluşturulmadı, atla
                    continue
            properties[prop_name] = prop_def

        response = self.client.databases.create(
            parent={"type": "page_id", "page_id": parent_page_id},
            title=[{"type": "text", "text": {"content": schema["name"]}}],
            properties=properties,
        )
        db_id = response["id"]
        logger.info("  ✓ %s: %s", schema["name"], db_id)
        return db_id

    def setup_all(self, parent_page_id: str) -> dict:
        """Tüm 5 veritabanını sırayla oluşturur."""
        logger.info("\nNotion veritabanları oluşturuluyor...")
        logger.info("Üst sayfa ID: %s\n", parent_page_id)

        env_vars = {}

        # Önce Hastalar (diğerleri buna relation kuruyor)
        logger.info("1. Hastalar DB oluşturuluyor...")
        patients_id = self.create_database(parent_page_id, PATIENTS_DB_SCHEMA)
        self.created_dbs["PATIENTS_DB"] = patients_id
        env_vars["NOTION_PATIENTS_DB_ID"] = patients_id

        # Sonra ilişkili DB'ler
        logger.info("2. Konsültasyonlar DB oluşturuluyor...")
        env_vars["NOTION_SESSIONS_DB_ID"] = self.create_database(parent_page_id, SESSIONS_DB_SCHEMA)

        logger.info("3. Ses Kayıtları DB oluşturuluyor...")
        env_vars["NOTION_AUDIO_RECORDS_DB_ID"] = self.create_database(parent_page_id, AUDIO_DB_SCHEMA)

        logger.info("4. Form Yanıtları DB oluşturuluyor...")
        env_vars["NOTION_FORM_RESPONSES_DB_ID"] = self.create_database(parent_page_id, FORMS_DB_SCHEMA)

        logger.info("5. Personel DB oluşturuluyor...")
        env_vars["NOTION_STAFF_DB_ID"] = self.create_database(parent_page_id, STAFF_DB_SCHEMA)

        return env_vars

    def print_env_instructions(self, env_vars: dict):
        """Kullanıcıya .env ayarlarını gösterir."""
        print("\n" + "=" * 60)
        print("BAŞARILI! Aşağıdaki satırları .env dosyanıza ekleyin:")
        print("=" * 60)
        for key, value in env_vars.items():
            print(f"{key}={value}")
        print("=" * 60)
        print("\nNot: Notion entegrasyonunuzu her veritabanıyla")
        print("paylaşmayı unutmayın (Share > Add connections).\n")


def main():
    parser = argparse.ArgumentParser(
        description="Klinik Otomasyon - Notion Veritabanı Kurulumu"
    )
    parser.add_argument(
        "--parent-page-id",
        required=True,
        help="Veritabanlarının oluşturulacağı Notion sayfa ID'si",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Notion API anahtarı (.env'den otomatik okunur)",
    )
    args = parser.parse_args()

    # API anahtarı
    api_key = args.api_key
    if not api_key:
        config = get_config()
        api_key = config.notion.api_key

    if not api_key:
        print("HATA: Notion API anahtarı bulunamadı.")
        print("  --api-key parametresi ile belirtin veya .env'e NOTION_API_KEY ekleyin.")
        sys.exit(1)

    setup = NotionSetup(api_key)
    try:
        env_vars = setup.setup_all(args.parent_page_id)
        setup.print_env_instructions(env_vars)
    except Exception as e:
        logger.error("Kurulum hatası: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
