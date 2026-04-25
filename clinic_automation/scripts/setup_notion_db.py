"""
Notion veritabanlarına gerekli property'leri ekler.
Kullanım: python -m clinic_automation.scripts.setup_notion_db
"""

from notion_client import Client
from clinic_automation.config.settings import get_config


def setup_databases():
    cfg = get_config()
    client = Client(auth=cfg.notion.api_key)

    patients_db = cfg.notion.patients_db_id
    sessions_db = cfg.notion.sessions_db_id
    audio_db = cfg.notion.audio_records_db_id
    form_db = cfg.notion.form_responses_db_id

    # 1. Hastalar DB
    if patients_db:
        print("Hastalar DB güncelleniyor...")
        client.databases.update(
            database_id=patients_db,
            properties={
                "Name": {"name": "İsim"},
                "Yaş": {"rich_text": {}},
                "Veli Adı": {"rich_text": {}},
                "Telefon": {"rich_text": {}},
                "Tanı": {"rich_text": {}},
                "Durum": {
                    "select": {
                        "options": [
                            {"name": "Aktif", "color": "green"},
                            {"name": "Pasif", "color": "gray"},
                            {"name": "Arşiv", "color": "brown"},
                        ]
                    }
                },
            },
        )
        print("  ✓ Hastalar OK (İsim, Yaş, Veli Adı, Telefon, Tanı, Durum)")

    # 2. Seanslar DB
    if sessions_db:
        print("Seanslar DB güncelleniyor...")
        props = {
            "Name": {"name": "Başlık"},
            "Tarih": {"date": {}},
            "Tanı": {"rich_text": {}},
        }
        if patients_db:
            props["Hasta"] = {
                "relation": {"database_id": patients_db, "single_property": {}}
            }
        client.databases.update(database_id=sessions_db, properties=props)
        print("  ✓ Seanslar OK (Başlık, Hasta, Tarih, Tanı)")

    # 3. Ses Kayıtları DB
    if audio_db:
        print("Ses Kayıtları DB güncelleniyor...")
        props = {
            "Name": {"name": "Başlık"},
            "Tarih": {"date": {}},
            "Dosya Adı": {"rich_text": {}},
            "Süre (sn)": {"number": {"format": "number"}},
            "Eşleşme Güveni": {"number": {"format": "percent"}},
            "Kalite": {
                "select": {
                    "options": [
                        {"name": "İyi", "color": "green"},
                        {"name": "Orta", "color": "yellow"},
                        {"name": "Düşük", "color": "red"},
                    ]
                }
            },
        }
        if patients_db:
            props["Hasta"] = {
                "relation": {"database_id": patients_db, "single_property": {}}
            }
        client.databases.update(database_id=audio_db, properties=props)
        print("  ✓ Ses Kayıtları OK (Başlık, Hasta, Tarih, Dosya Adı, Süre, Güven, Kalite)")

    # 4. Form Yanıtları DB
    if form_db:
        print("Form Yanıtları DB güncelleniyor...")
        props = {
            "Name": {"name": "Başlık"},
            "Form Tarihi": {"date": {}},
            "Şikayet": {"rich_text": {}},
        }
        if patients_db:
            props["Hasta"] = {
                "relation": {"database_id": patients_db, "single_property": {}}
            }
        client.databases.update(database_id=form_db, properties=props)
        print("  ✓ Form Yanıtları OK (Başlık, Hasta, Form Tarihi, Şikayet)")

    print("\nTüm veritabanları hazır!")


if __name__ == "__main__":
    setup_databases()
