# Acil Kurulum Rehberi (30-45 dk)

WhatsApp botunu en kısa sürede ayağa kaldırmak için adım adım kılavuz.
Bot hasta numarasını tanır, Notion'dan dosyasını çeker, Claude ile akıllı yanıt üretir.

---

## Ön Koşullar (Bilgisayarınızda)

- Python 3.10+ (`python3 --version`)
- Git
- OpenSSL (macOS/Linux'ta hazır, Windows için Git Bash yeterli)
- Bir metin editörü (nano, VS Code, vb.)

---

## Aşama 1 — API Hesapları (yaklaşık 15 dk)

### 1.1 Twilio (WhatsApp)

1. https://www.twilio.com/try-twilio → Ücretsiz hesap aç (kredi kartı gerekmiyor, sandbox için)
2. Console → **Account Info** panelinden şunları kopyalayın:
   - `Account SID`
   - `Auth Token` (göz ikonuna tıklayarak göster)
3. **Messaging → Try it out → Send a WhatsApp message**
4. Ekrandaki talimatı takip edin: kendi WhatsApp'ınızdan `+1 415 523 8886` numarasına verilen kelimeyi gönderin (ör: `join tree-fox`)
5. Şimdi Twilio sandbox aktif. Mesaj göndermek için bu 5 kelime: **Account SID**, **Auth Token**, sandbox numarası.

### 1.2 Notion (Hasta Veritabanı)

1. https://www.notion.so/my-integrations → **New integration**
2. İsim: `Klinik Otomasyon` | Workspace: kendi workspace'iniz
3. **Capabilities**: Read ✓, Update ✓, Insert ✓
4. Submit → "Internal Integration Secret" → kopyalayın (`secret_xxx`)

### 1.3 Anthropic (Claude — Akıllı Cevaplar)

1. https://console.anthropic.com/settings/keys → **Create Key**
2. İsim: `clinic-bot` → Create → anahtarı kopyalayın (`sk-ant-xxx`)

### 1.4 OpenAI (Whisper — Ses Transkripsiyonu, opsiyonel)

> Eğer ilk aşamada sadece WhatsApp yanıtlaması önemliyse bu adımı atlayabilirsiniz.

1. https://platform.openai.com/api-keys → **Create new secret key**
2. Anahtarı kopyalayın (`sk-xxx`)

---

## Aşama 2 — Kurulum (5 dk)

```bash
git clone <bu_repo_url>
cd clinic_automation
bash scripts/quickstart.sh
```

Script otomatik yapar:
- Python sanal ortamı + bağımlılıklar
- `.env` dosyası (şablondan kopya)
- RSA 4096-bit şifreleme anahtarları
- Dizin yapısı

---

## Aşama 3 — `.env` Doldurma (5 dk)

```bash
nano .env
```

En az şu satırları doldurun:

```ini
# WhatsApp
WHATSAPP_PROVIDER=twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxx
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886

# Notion
NOTION_API_KEY=secret_xxxxxxxxxx
# DB ID'leri Aşama 4'te doldurulacak

# Claude
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxx

# Chatbot
CHATBOT_ENABLED=true
CHATBOT_CONFIDENCE_THRESHOLD=0.70
MESSAGING_HOURS_START=8
MESSAGING_HOURS_END=21
```

Kaydedin (nano'da `Ctrl+O`, `Enter`, `Ctrl+X`).

---

## Aşama 4 — Notion Veritabanları (10 dk)

### 4.1 Üst sayfa oluşturun

1. Notion'da yeni bir sayfa: "Klinik Sistemi"
2. Sağ üst **⋯ → Add connections → Klinik Otomasyon** (Aşama 1.2'de oluşturduğunuz entegrasyon)
3. Sayfanın URL'sinden `PAGE_ID`'yi alın:

```
https://notion.so/workspace/Klinik-Sistemi-abc123def456...
                                             ^^^^^^^^^^^^
                                             Bu kısmı kopyalayın
```

### 4.2 Scripti çalıştırın

```bash
source .venv/bin/activate
python scripts/notion_setup.py --parent-page-id abc123def456
```

Çıktı:

```
1. Hastalar DB oluşturuluyor...  ✓
2. Konsültasyonlar DB oluşturuluyor...  ✓
...
BAŞARILI! Aşağıdaki satırları .env dosyanıza ekleyin:
NOTION_PATIENTS_DB_ID=...
NOTION_SESSIONS_DB_ID=...
...
```

### 4.3 DB ID'lerini `.env`'e yapıştırın

```bash
nano .env
# Aşağıdaki 5 satırı düzenleyin
```

### 4.4 İlk hastayı ekleyin (TEST için)

Notion'da "Hastalar" veritabanını açın, yeni bir satır ekleyin:
- **İsim**: (test için kendi adınız)
- **Telefon**: (kendi WhatsApp numaranız, `+905551234567` formatında)

---

## Aşama 5 — Sunucuyu Başlat (3 dk)

### 5.1 Webhook sunucusu

**Terminal 1:**
```bash
source .venv/bin/activate
python scripts/webhook_server.py
```

Başlangıçta şunu görmelisiniz:
```
INFO: Notion entegrasyonu aktif - hasta dosyalarına erişim var.
INFO: Claude LLM aktif - akıllı yanıtlar devrede.
INFO: WhatsApp Webhook Sunucusu başlatılıyor (port: 5000)
```

### 5.2 Ngrok ile public URL (geliştirme)

**Terminal 2:**
```bash
# Ngrok'u indir: https://ngrok.com/download
ngrok http 5000
```

Çıktı:
```
Forwarding    https://xxxx-xx-xx-xx.ngrok-free.app -> http://localhost:5000
```

Bu URL'i kopyalayın.

### 5.3 Twilio'ya Webhook URL'ini ekleyin

1. Twilio Console → **Messaging → Settings → WhatsApp sandbox settings**
2. **When a message comes in:**
   ```
   https://xxxx-xx-xx-xx.ngrok-free.app/webhook/twilio
   ```
   Method: **POST**
3. **Save**

---

## Aşama 6 — Test (2 dk)

Kendi telefonunuzdan Twilio sandbox numarasına (Aşama 1.1'de aktifleştirdiniz) mesaj atın:

```
Merhaba, randevum ne zaman?
```

### Beklenen davranış:
- Sunucu terminalinde: `INFO: Twilio mesajı: +9055... -> 'Merhaba, randevum ne zaman?'`
- WhatsApp'ta birkaç saniye sonra bot yanıt verir
- Mesajda kişisel selamlama olmalı (Notion'da kayıtlı adınızla)
- Claude akıllı yanıt ürettiyse cevap doğal, kısa olacak

### Sağlık kontrolü

Tarayıcıda: `http://localhost:5000/health`

```json
{
  "status": "ok",
  "chatbot": "enabled",
  "provider": "twilio",
  "notion": "connected",
  "llm": "claude"
}
```

---

## Sorun Giderme

| Sorun | Çözüm |
|---|---|
| `HATA: Notion API anahtarı bulunamadı` | `.env`'de `NOTION_API_KEY` dolu mu? |
| Twilio'dan mesaj geliyor ama bot cevap vermiyor | Ngrok URL'i doğru mu? `Ctrl+C` ile yeniden başlatıp yeni URL'i Twilio'ya tekrar yazın |
| Bot "hasta dosyasını" göremiyor | Hastanın telefon numarası Notion'da `+905551234567` formatında mı? |
| "ACİL durumda 112'yi arayınız" yanıtı sürekli geliyor | Mesaj saatleri dışında olabilirsiniz. `MESSAGING_HOURS_START=0`, `END=23` yapın. |
| `ModuleNotFoundError` | `source .venv/bin/activate` unutmayın |

---

## Production (Sonraki Adım)

Ngrok geçicidir. Kalıcı çözüm için:

**VPS (DigitalOcean, Hetzner, AWS):**
```bash
# Sunucuda
git clone <repo>
cd clinic_automation
bash scripts/quickstart.sh
# .env'i doldurun
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:5000 scripts.webhook_server:app
```

Nginx + SSL (Let's Encrypt) ile HTTPS ekleyin, Twilio'da URL'i güncelleyin.

**systemd servisi** ile otomatik başlatma:
```ini
# /etc/systemd/system/clinic-webhook.service
[Unit]
Description=Klinik Webhook
After=network.target

[Service]
User=klinik
WorkingDirectory=/opt/clinic_automation
EnvironmentFile=/opt/clinic_automation/.env
ExecStart=/opt/clinic_automation/.venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 scripts.webhook_server:app
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now clinic-webhook
```

---

## Güvenlik Checklistesi (KVKK)

- [ ] `.env` dosyası `.gitignore`'da (commit edilmedi)
- [ ] `.rsa_key.pem` güvenli yedeklendi (ayrı fiziksel konum)
- [ ] Sunucu HTTPS ile erişiliyor (production'da)
- [ ] Twilio imza doğrulaması açık (`TWILIO_AUTH_TOKEN` dolu olduğunda otomatik)
- [ ] `FIELD_LEVEL_ENCRYPTION=true`
- [ ] Audit log (`audit.log`) düzenli yedeklenyor
- [ ] Veri saklama süreleri (`DATA_RETENTION_DAYS=2555`) yapılandırıldı

---

## Destek Komutları

```bash
# Sistem durumu
python -m clinic_automation.main status

# Audit log bütünlük kontrolü
python -m clinic_automation.main audit --verify

# Testleri çalıştır
python -m pytest tests/ -v
```
