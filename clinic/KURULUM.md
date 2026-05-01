# Klinik Sistemi — Adım Adım Kurulum

> **Hedef:** Notion + Google Calendar + Google Forms + Evolution API + Paraşüt entegrasyonlarını üretime hazır hale getirmek.
>
> **Süre:** Her servis için ortalama 10–15 dk (toplam 1–2 saat).

İçindekiler:
1. [Notion](#1-notion)
2. [Google Cloud — Calendar + Forms](#2-google-cloud--calendar--forms)
3. [Evolution API — WhatsApp](#3-evolution-api--whatsapp)
4. [Paraşüt — e-SMM](#4-paraşüt--e-smm)
5. [Ollama — Yerel LLM](#5-ollama--yerel-llm)
6. [HuggingFace — PyAnnote](#6-huggingface--pyannote-modeli)
7. [Test ve Doğrulama](#7-test-ve-doğrulama)

---

## 0. Önkoşullar

- Python 3.11+ kurulu
- `clinic/` dizininde olduğunuzu doğrulayın
- Bağımlılıklar: `pip install -r clinic/requirements.txt`
- `.env` dosyası: `cp clinic/.env.example clinic/.env`
- Secret'lar üretildi: `python clinic/scripts/generate_secrets.py >> clinic/.env`

---

## 1. Notion

### 1.1. Integration token oluştur

1. https://www.notion.so/my-integrations adresine git
2. **"+ New integration"** tıkla
3. Form:
   - **Name:** `Klinik Sistemi`
   - **Logo:** İsteğe bağlı
   - **Associated workspace:** Klinik için kullanacağın workspace
   - **Type:** **Internal** (sağlık verisi → asla "Public" olmamalı)
4. **Submit** → Integration sayfası açılır
5. **"Internal Integration Secret"** kısmından **Show** → token'ı kopyala (`secret_...`)

📝 **`.env`'e yaz:**
```
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 1.2. Parent page hazırla

Database'leri otomatik oluşturmak için bir parent page gerek:

1. Notion'da yeni bir page oluştur (örn. "🏥 Klinik")
2. Sağ üst **⋯** → **Connections** → **Add connection** → `Klinik Sistemi`'ni seç
3. Page URL'sinden ID'yi kopyala:
   ```
   https://www.notion.so/Klinik-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
                                 └──────── 32 karakter ────────┘
   ```

### 1.3. Database'leri otomatik oluştur

```bash
python clinic/scripts/setup_notion.py \
    --parent-page <PARENT_PAGE_ID> \
    --update-env clinic/.env
```

Bu komut iki database oluşturur:
- 🏥 **Hastalar** — Hasta Adı, Randevu Tarihi, Randevu ID, Durum, Veli Telefonu
- 💊 **İlaçlar** — İlaç, Hasta, Doz, Başlangıç, Bitiş, Durum, Notlar

`.env`'e `NOTION_DATABASE_ID` ve `NOTION_MEDICATIONS_DATABASE_ID` otomatik yazılır.

### 1.4. KVKK — Hibrit mod (önerilir)

Çocuk psikiyatrisi verisi için KVKK Hibrit mod **şiddetle önerilir**:

```
KVKK_HYBRID_MODE=true
```

Aktif olduğunda Notion'a **gerçek isim ve TCKN gitmez** — sadece pseudonym (`#a4f9-c2b1`) ve klinik notlar. Gerçek PII yerel `patient_registry.db`'de Fernet ile şifreli kalır.

### 1.5. Test
```bash
python clinic/scripts/test_connections.py --only notion
```
Beklenen:
```
✓ Bağlandı: Klinik Sistemi (bot)
✓ Hasta DB erişilebilir: 5 property
✓ Tüm gerekli property'ler mevcut
```

---

## 2. Google Cloud — Calendar + Forms

### 2.1. GCP projesi

1. https://console.cloud.google.com → **Select project** → **NEW PROJECT**
2. Project name: `klinik-sistem` (veya benzeri)
3. **CREATE**

### 2.2. API'leri etkinleştir

Sol menü → **APIs & Services** → **Library**:
- **Google Calendar API** → ENABLE
- **Google Forms API** → ENABLE

### 2.3. OAuth consent ekranı

Sol menü → **APIs & Services** → **OAuth consent screen**:

1. **User Type:** External (Desktop app için)
2. **App information:**
   - App name: `Klinik Sistemi`
   - User support email: kendi e-postanız
   - Developer contact: kendi e-postanız
3. **Scopes** (SAVE AND CONTINUE'da):
   - **Add or remove scopes** → şunları işaretle:
     - `.../auth/calendar.readonly`
     - `.../auth/forms.responses.readonly`
4. **Test users:**
   - **+ ADD USERS** → kendi Gmail adresinizi ekleyin
   - (External + test users yeterli; "PUBLISH APP" yapmaya gerek yok)
5. **SAVE AND CONTINUE**

### 2.4. OAuth Client ID

Sol menü → **APIs & Services** → **Credentials**:

1. **+ CREATE CREDENTIALS** → **OAuth client ID**
2. **Application type:** **Desktop app**
3. **Name:** `Klinik Sistemi Desktop`
4. **CREATE** → JSON download göster
5. İndirilen dosyayı `clinic/credentials.json` olarak yerleştir

### 2.5. Calendar ID ve scope ayarları

`.env`:
```
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json
GOOGLE_CALENDAR_ID=primary
```

> **Not:** `primary` = ana takvim. Ayrı bir "Klinik" takvimi oluşturduysan, takvimin ayarlarından **Integrate calendar** → **Calendar ID**'yi al.

### 2.6. Anamnez formu

1. https://forms.google.com → yeni form oluştur
2. Sorular ekle (Yaş, Şikâyet, Anamnez, Aile öyküsü, vs.)
3. **Settings** → **Responses** → **Restrict to users** kapalı (anonim cevap)
4. Form URL:
   ```
   https://docs.google.com/forms/d/<FORM_ID>/viewform
                                    └─ bu ID
   ```
5. Veliye gönderilecek kısa link: **Send** → **🔗 Link** → Shorten URL

`.env`:
```
GOOGLE_ANAMNESIS_FORM_ID=<FORM_ID>
GOOGLE_ANAMNESIS_FORM_URL=https://forms.gle/xxx
```

### 2.7. İlk yetkilendirme (token.json üret)

```bash
cd clinic
python -c "from module1_transcription_engine import get_calendar_service; get_calendar_service()"
```

İlk çağrıda tarayıcı açılır → Gmail hesabınla giriş yap → izin ver → `token.json` üretilir.

### 2.8. Test
```bash
python clinic/scripts/test_connections.py --only calendar
python clinic/scripts/test_connections.py --only forms
```

---

## 3. Evolution API — WhatsApp

> Evolution API, WhatsApp Web'in unofficial REST sarmalayıcısı. Self-hosted veya hosted seçenek var. Klinik için **self-hosted** (Türkiye sunucu) öneriyorum.

### 3.1. Self-hosted kurulum

`docker-compose.yml`:
```yaml
services:
  evolution:
    image: atendai/evolution-api:latest
    container_name: evolution
    restart: always
    ports:
      - "8080:8080"
    environment:
      - AUTHENTICATION_API_KEY=evo_yourkey_here
      - DATABASE_ENABLED=false
      - REDIS_ENABLED=false
    volumes:
      - ./evolution_data:/evolution/instances
```

`docker-compose up -d` ile çalıştır.

### 3.2. Instance oluştur

Tarayıcı: `http://localhost:8080/manager`
- Login: API key
- **Instance** → **Create instance**
  - Name: `clinic`
  - WebHook URL: `https://yourdomain.com/webhook/whatsapp`
  - Event types: `MESSAGES_UPSERT, CONNECTION_UPDATE`

### 3.3. QR taratma

- Instance kartı → **QR Code** → telefondan WhatsApp → bağlı cihazlar → QR taratın
- State: `open` olmalı

### 3.4. .env
```
EVOLUTION_API_URL=http://localhost:8080
EVOLUTION_API_KEY=evo_yourkey_here
EVOLUTION_INSTANCE_NAME=clinic
WEBHOOK_PUBLIC_URL=https://yourdomain.com
DOCTOR_PHONE=905321234567   # iletişim için kendi numaranız
```

### 3.5. Test
```bash
python clinic/scripts/test_connections.py --only evolution
```

---

## 4. Paraşüt — e-SMM

### 4.1. API erişim talebi

1. https://uygulama.parasut.com → **Ayarlar** → **API Erişimi**
2. **API erişimi etkinleştir** → e-imzanızı yükleyin
3. `client_id`, `client_secret` üretilir

### 4.2. Şirket ID
Browser URL'sinden alın:
```
https://uygulama.parasut.com/12345/dashboard
                              └─ COMPANY_ID
```

### 4.3. SMM kategori ID

1. **Ayarlar** → **Genel** → **Kategoriler**
2. SMM için yeni kategori ekleyin (örn. "Psikiyatri Muayenesi")
3. URL'den ID'yi alın

### 4.4. .env
```
PARASUT_CLIENT_ID=xxx
PARASUT_CLIENT_SECRET=xxx
PARASUT_USERNAME=doktor@klinik.com
PARASUT_PASSWORD=xxx
PARASUT_COMPANY_ID=12345
PARASUT_SMM_CATEGORY_ID=67890

VAT_RATE=0
WITHHOLDING_RATE=20            # ⚠ MALİ MÜŞAVİRİNİZLE TEYİT EDİN
VAT_WITHHOLDING_RATE=0
```

> **🚨 KRİTİK:** Vergi oranlarını MALİ MÜŞAVİRİNİZLE TEYİT EDİN. Yanlış oran → eksik vergi kesimi → idari ceza.

### 4.5. Test
```bash
python clinic/scripts/test_connections.py --only parasut
```

---

## 5. Ollama — Yerel LLM

> Modül 1 SOAP üretimini yerel LLM ile yapar — hasta verisi **asla** dış API'ye gitmez.

### 5.1. Kurulum
- macOS/Linux: `curl -fsSL https://ollama.com/install.sh | sh`
- Windows: https://ollama.com/download → installer

### 5.2. Model indir
```bash
ollama pull llama3       # 8B, 4.7 GB
# veya daha güçlü:
ollama pull llama3:70b   # 70B, 40 GB (RAM/VRAM gerektirir)
```

### 5.3. .env
```
OLLAMA_MODEL=llama3
```

### 5.4. Test
```bash
python clinic/scripts/test_connections.py --only ollama
```

---

## 6. HuggingFace — PyAnnote modeli

> Modül 1 konuşmacı ayrımı için PyAnnote modeli. Erişim onayı gerek.

### 6.1. HF token
1. https://huggingface.co/settings/tokens → **New token** (Read scope yeterli)
2. Kopyala

### 6.2. Model erişim onayı
3. https://huggingface.co/pyannote/speaker-diarization-3.1 → **Agree and access repository**
4. Aynı şeyi `pyannote/segmentation-3.0` için de yap

### 6.3. .env
```
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
WHISPER_MODEL_SIZE=large-v3
```

---

## 7. Test ve Doğrulama

### 7.1. Tüm bağlantıları test et
```bash
python clinic/scripts/test_connections.py
```
Beklenen: tüm aktif servisler ✓.

### 7.2. Preflight (üretim öncesi)
```bash
python clinic/scripts/preflight_check.py
```
Beklenen: 0 hata.

### 7.3. Sistem testi
```bash
python clinic/main.py --webhook-only
```
Tarayıcı: `http://localhost:5055/ui/login` → ADMIN_TOKEN ile giriş.

Dashboard'da:
- WhatsApp: ✓ Bağlı
- State Store: ✓ Aktif
- Hasta listesinde test kayıtları
- Audit log'da erişim izleri

### 7.4. Üretime alma

`clinic/DEPLOYMENT.md`'yi takip edin:
- Türkiye'de VPS (Vargonen, Turkcell Bulut, vs.)
- nginx + Let's Encrypt + systemd
- Yedekleme cron'u
- Sentry / log monitoring

---

## ❓ Sorun giderme

### "credentials.json bulunamadı"
- `clinic/credentials.json` dosyası var mı?
- `.env`'de `GOOGLE_CREDENTIALS_FILE=credentials.json`?
- Çalışma dizini `clinic/` mi?

### "Notion 401 Unauthorized"
- Token doğru mu kopyalandı? (`secret_` prefix dahil)
- Parent page integration'a paylaşıldı mı? (⋯ → Connections)

### "WhatsApp instance state: close"
- Evolution panelden QR'i tekrar tarat
- Telefonun WhatsApp'ı internete bağlı mı?

### "Paraşüt token alınamadı"
- API erişimi etkinleştirildi mi?
- E-imza Paraşüt'e yüklendi mi?
- 2FA varsa app password kullan

### "Ollama daemon çalışıyor mu?"
```bash
ollama serve   # ayrı bir terminalde
ollama list    # modelleri listele
```

---

## 📞 Destek

- KVKK soruları → KVKK uzmanı / hukuk danışmanı (zorunlu)
- Vergi soruları → mali müşavir (zorunlu)
- Teknik sorunlar → bu repo Issues
