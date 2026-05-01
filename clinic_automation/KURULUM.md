# Klinik Otomasyon Sistemi - Kurulum ve Kullanım Kılavuzu

## Sistem Mimarisi

```
┌─────────────────────────────────────────────────────────────────┐
│                    KLİNİK OTOMASYON SİSTEMİ                     │
├─────────────┬──────────────┬──────────────┬─────────────────────┤
│  Google     │  WhatsApp    │  Ses İşleme  │  Yapay Zeka         │
│  Calendar   │  (Twilio/    │  (Whisper)   │  (Claude/GPT)       │
│  Forms      │   Evolution) │              │                     │
├─────────────┴──────────────┴──────────────┴─────────────────────┤
│              AKILLI EŞLEŞTİRME ALGORİTMASI                      │
│  (Dosya adı + Calendar zamanı + Transkript içerik analizi)      │
├─────────────────────────────────────────────────────────────────┤
│                    NOTION VERİTABANI                             │
│  (Hastalar | Seanslar | Klinik Notlar)                          │
├─────────────────────────────────────────────────────────────────┤
│              GÜVENLİK KATMANI (KVKK)                            │
│  (Fernet Şifreleme | Denetim Kaydı | Erişim Kontrolü)          │
└─────────────────────────────────────────────────────────────────┘
```

## Hızlı Kurulum

### 1. Gereksinimler
- Python 3.11+
- ffmpeg (ses dönüştürme için): `sudo apt install ffmpeg`

### 2. Kurulum
```bash
cd clinic_automation
pip install -r requirements.txt
pip install -e .
```

### 3. Konfigürasyon
```bash
cp .env.example .env
# .env dosyasını düzenleyip API anahtarlarınızı girin
```

## API Kurulumları

### Google Calendar & Forms
1. [Google Cloud Console](https://console.cloud.google.com/) -> Yeni proje
2. Calendar API ve Forms API'yi etkinleştirin
3. OAuth 2.0 kimlik bilgileri oluşturun
4. `credentials.json` dosyasını proje kök dizinine koyun
5. İlk çalıştırmada tarayıcıda yetkilendirme yapılacak

### Notion
1. [Notion Integrations](https://www.notion.so/my-integrations) -> Yeni entegrasyon
2. API anahtarını `.env` dosyasına yazın
3. Notion'da 3 veritabanı oluşturun:
   - **Hastalar DB**: İsim (title), Yaş, Veli Adı, Telefon, Tanı, Durum (select)
   - **Seanslar DB**: Başlık (title), Hasta (relation->Hastalar), Tarih (date), Tanı
   - **Notlar DB**: (opsiyonel, ek notlar için)
4. Her veritabanını entegrasyonunuzla paylaşın
5. Veritabanı ID'lerini `.env` dosyasına yazın

### WhatsApp (Twilio)
1. [Twilio Console](https://www.twilio.com/console) -> Hesap oluşturun
2. WhatsApp Sandbox'ı etkinleştirin (test için) veya Business profil onayı alın
3. Account SID, Auth Token ve WhatsApp numarasını `.env`'ye yazın

### WhatsApp (Evolution API - Alternatif)
1. Evolution API'yi self-hosted olarak kurun
2. `.env`'de `WHATSAPP_PROVIDER=evolution` yapın
3. API URL, key ve instance bilgilerini girin

### Transkripsiyon
**OpenAI Whisper API (önerilen):**
- OpenAI API anahtarını `.env`'ye yazın

**Local Whisper (ücretsiz, GPU önerilir):**
- `.env`'de `TRANSCRIPTION_PROVIDER=local` yapın
- `pip install faster-whisper` (CUDA destekli GPU varsa çok daha hızlı)

### LLM (Klinik Not Üretimi)
**Anthropic Claude (önerilen):**
- API anahtarını `.env`'ye yazın
- `LLM_PROVIDER=anthropic`

**OpenAI GPT-4:**
- `LLM_PROVIDER=openai`

## Kullanım

### Sistem Durumu Kontrolü
```bash
clinic status
```

### Ses Dosyalarını İşle
```bash
# Varsayılan dizinden tüm dosyaları işle
clinic process

# Belirli dizin ve tarih
clinic process --dir /path/to/audio --date 2024-03-15

# Önce simülasyon yap
clinic process --dry-run
```

### Randevu Hatırlatma Gönder
```bash
# Yarınki randevular için
clinic remind

# 3 gün sonrası için
clinic remind --days 3
```

### Calendar Senkronizasyonu
```bash
clinic sync --days 14
```

### Samsung Notes Migrasyon
```bash
# Önce simüle et
clinic migrate /path/to/samsung_notes --dry-run

# Gerçek aktarım
clinic migrate /path/to/samsung_notes
```

## Akıllı Eşleştirme Algoritması

### Nasıl Çalışır?

Sistem 3 veri kaynağını çapraz referanslar:

| Kaynak | Ağırlık | Açıklama |
|--------|---------|----------|
| Dosya adı | %35 | Dosya adındaki hasta ismi ile Calendar karşılaştırması |
| Calendar zamanı | %30 | Ses dosyası zamanı ile randevu blokları sıralaması |
| Transkript içeriği | %25 | Metinde isim geçişi ve bağlam değişikliği tespiti |
| Süre uyumu | %10 | Kayıt süresi ile randevu süresi karşılaştırması |

### Edge Case Yönetimi

**Çoklu hasta (tek dosyada 2-3 görüşme):**
- "Hoş geldin" / "İyi günler" gibi geçiş kalıpları aranır
- 10+ saniyelik sessizlik boşlukları tespit edilir
- Her bölüm ayrı hastayla eşleştirilir

**Parçalı kayıtlar (part 1, part 2...):**
- Dosya adında "part", "parça", "(1)" gibi kalıplar aranır
- Aynı hasta + aynı tarih olan parçalar birleştirilir

**İsimsiz kayıtlar:**
- Calendar zaman sıralaması ile korelasyon
- Transkriptteki isim ipuçları
- Güven skoru düşükse "İnceleme Gerekli" olarak işaretlenir

## Güvenlik (KVKK Uyumu)

- Tüm ses dosyaları ve transkriptler **Fernet (AES-128-CBC)** ile şifrelenir
- Şifreleme anahtarı `0600` izinlerle korunur
- Her veri erişimi **denetim kaydına** yazılır (JSON format, checksum doğrulamalı)
- API anahtarları `.env` dosyasında tutulur (repo'ya dahil edilmez)
- Veri saklama süresi konfigüre edilebilir (varsayılan: 365 gün)

## Dosya Yapısı
```
clinic_automation/
├── config/
│   └── settings.py          # Merkezi konfigürasyon
├── modules/
│   ├── google_calendar.py   # Google Calendar entegrasyonu
│   ├── google_forms.py      # Google Forms entegrasyonu
│   ├── whatsapp.py          # WhatsApp otomasyon (Twilio/Evolution)
│   ├── transcription.py     # Ses transkripsiyon (Whisper)
│   ├── smart_matcher.py     # Akıllı eşleştirme algoritması
│   ├── clinical_notes.py    # LLM ile klinik not üretimi
│   └── notion_client.py     # Notion API entegrasyonu
├── migrations/
│   └── samsung_notes.py     # Samsung Notes -> Notion aktarımı
├── templates/
│   └── clinical_note_template.py
├── utils/
│   ├── helpers.py           # Yardımcı fonksiyonlar
│   └── security.py          # Şifreleme ve denetim
├── tests/
├── main.py                  # CLI ve orkestratör
├── requirements.txt
├── setup.py
├── .env.example
└── KURULUM.md
```

## Önerilen Geliştirme Yol Haritası

### Faz 1 - Temel Altyapı (1-2 hafta)
1. `.env` konfigürasyonunu tamamla
2. Google API OAuth kurulumu yap
3. Notion veritabanlarını oluştur
4. `clinic status` ile bağlantıları test et

### Faz 2 - Transkripsiyon (1 hafta)
1. Birkaç ses dosyası ile transkripsiyon testi
2. Eşleştirme algoritmasını `--dry-run` ile dene
3. Güven skorlarını incele, gerekirse eşikleri ayarla

### Faz 3 - Tam Otomasyon (1-2 hafta)
1. Calendar senkronizasyonunu çalıştır
2. Klinik not üretimini test et
3. Samsung Notes migrasyonunu yap

### Faz 4 - WhatsApp Entegrasyonu (1 hafta)
1. Twilio sandbox ile test
2. Hatırlatma mesajlarını dene
3. Form gönderimini test et
