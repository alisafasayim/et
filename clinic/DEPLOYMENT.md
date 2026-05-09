# Klinik Sistemi — Üretim Kurulum Kılavuzu

KVKK uyumlu hibrit mimari ile çocuk-ergen psikiyatrisi otomasyon sisteminin
sıfırdan üretim sunucusuna kurulumu.

> **KVKK m.9 notu**: Hasta verisinin yurt dışına aktarımını engellemek için
> sunucu **Türkiye'de** olmalıdır. Vargonen, Turkticaret, Turhost gibi
> sağlayıcılar uygundur.

---

## 0. Ön gereksinimler

| Bileşen | Önerilen |
|---|---|
| Sunucu | Ubuntu 22.04+ LTS, **Türkiye lokasyonu** |
| CPU | 4 vCPU |
| RAM | 8 GB (Whisper large-v3 için 5 GB serbest) |
| Disk | 50 GB SSD |
| Domain | klinik.example.com (HTTPS için) |
| GPU | (opsiyonel) NVIDIA — Whisper hızı 5×+ |

Hesaplar:
- Google Cloud (Calendar + Forms API)
- Notion workspace + Integration
- Paraşüt hesabı + API uygulaması
- HuggingFace hesabı (pyannote model erişimi için)
- Evolution API (self-host) için Docker

---

## 1. Sistem paketleri

```bash
sudo apt update
sudo apt install -y \
    python3.11 python3.11-venv python3-pip \
    ffmpeg git curl \
    nginx certbot python3-certbot-nginx \
    sqlite3
```

`ffmpeg` faster-whisper için zorunludur. `python3.11+` `zoneinfo` desteği için
gereklidir.

---

## 2. Servis kullanıcısı + repo

```bash
sudo useradd -r -m -s /bin/bash clinic
sudo su - clinic

git clone https://github.com/alisafasayim/et.git
cd et/clinic

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> ML paketleri (faster-whisper, pyannote.audio) ilk kurulumda 1-2 GB indirir
> ve birkaç dakika sürer. Kullanmıyorsanız `requirements.txt`'ten Modül 1
> bloğunu çıkarabilirsiniz.

---

## 3. Secret/key üretimi

```bash
# Şablon .env'i kopyala
cp .env.example .env

# Tüm gerekli secret/key'leri üret ve .env'e ekle
python scripts/generate_secrets.py --append .env

# Dosya iznini sıkılaştır — sadece sahibi okusun
chmod 600 .env
```

Bu adımda üretilen `PII_ENCRYPTION_KEY`'i **mutlaka yedekleyin** (parola
yöneticisi, offline kasa). Kayboldursa şifreli hasta verisi çözülemez.

---

## 4. Google OAuth (Calendar + Forms)

1. https://console.cloud.google.com → yeni proje
2. **APIs & Services → Library** → "Google Calendar API" ve "Google Forms API" enable
3. **APIs & Services → OAuth consent screen** → External, app adı "Klinik"
4. **APIs & Services → Credentials** → Create Credentials → OAuth client ID → Desktop app
5. JSON'ı indir, sunucuda `clinic/credentials.json` olarak kaydet:
   ```bash
   nano /home/clinic/et/clinic/credentials.json   # içine yapıştır
   chmod 600 /home/clinic/et/clinic/credentials.json
   ```
6. İlk çalıştırmada token üret:
   ```bash
   cd /home/clinic/et/clinic
   source .venv/bin/activate
   python -c "from module1_transcription_engine import get_calendar_service; get_calendar_service()"
   ```
   Tarayıcıda açılan onay sayfasını tamamla → `token.json` üretilir.

---

## 5. Notion DB kurulumu

### 5a. Integration

1. https://notion.com/my-integrations → "New integration" → "Klinik"
2. Internal integration token'ı kopyala → `.env` içinde `NOTION_TOKEN=`

### 5b. "Hastalar" veritabanı

Notion'da yeni bir database oluştur. **Hierarchical mode** kullanacaksanız
sadece `Hasta Adı` (title) zorunlu; flat mode için aşağıdaki tüm
property'ler.

| Property | Tip |
|---|---|
| Hasta Adı | Title |
| Randevu Tarihi | Date |
| Randevu ID | Rich Text |
| Durum | Select (`Arşivlendi` seçeneği) |

Database sayfa menüsünden **"Connections → Add connection → Klinik"**.
URL'den DB ID'yi al (32 hane, örn. `?v=...&p=` arasındaki bölüm) →
`.env` içinde `NOTION_DATABASE_ID=`.

### 5c. (Opsiyonel) "İlaçlar" veritabanı

| Property | Tip |
|---|---|
| İlaç | Title |
| Hasta | Rich Text |
| Doz | Rich Text |
| Başlangıç | Date |
| Bitiş | Date |
| Durum | Select (`Aktif` / `Sonlandırıldı` / `Değiştirildi`) |
| Notlar | Rich Text |

ID'yi al → `.env` içinde `NOTION_MEDICATIONS_DATABASE_ID=`.

---

## 6. Evolution API (WhatsApp)

Self-host (Docker önerilir):

```bash
sudo apt install -y docker.io docker-compose
sudo usermod -aG docker clinic   # logout/login gerekli
```

```bash
mkdir -p ~/evolution && cd ~/evolution
cat > docker-compose.yml <<'YAML'
version: "3.9"
services:
  evolution:
    image: atendai/evolution-api:latest
    container_name: evolution
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:8080"   # sadece localhost — nginx açacak
    environment:
      AUTHENTICATION_API_KEY: <GIZLI_API_KEY>
      LOG_LEVEL: INFO
    volumes:
      - ./instances:/evolution/instances
YAML
docker compose up -d
```

Tarayıcıda http://localhost:8080/manager → instance oluştur (`clinic`),
QR kodu telefonunla taratıp WhatsApp'ı bağla.

`.env`'de:
```
EVOLUTION_API_URL=http://localhost:8080
EVOLUTION_API_KEY=<GIZLI_API_KEY>
EVOLUTION_INSTANCE_NAME=clinic
```

---

## 7. Paraşüt OAuth (e-SMM)

1. https://uygulama.parasut.com → Ayarlar → API → Yeni uygulama
2. Client ID + Client Secret al
3. `.env`:
   ```
   PARASUT_CLIENT_ID=...
   PARASUT_CLIENT_SECRET=...
   PARASUT_USERNAME=doktor@klinik.com
   PARASUT_PASSWORD=...
   PARASUT_COMPANY_ID=...        # URL'den (uygulama.parasut.com/<id>/...)
   PARASUT_SMM_CATEGORY_ID=...   # Ayarlar → Hesap planı'ndan
   ```

> ⚠️ **MALİ MÜŞAVİRLE TEYİT EDİN**:
> `VAT_RATE`, `WITHHOLDING_RATE`, `VAT_WITHHOLDING_RATE` doktorunuzun
> vergi rejimine göre doğru olmalı. Varsayılan %0/%20/%0 yaygın değer
> ama sizin durumunuza uymayabilir.

---

## 8. HuggingFace token (PyAnnote)

1. https://huggingface.co/settings/tokens → "Read" token oluştur
2. https://huggingface.co/pyannote/speaker-diarization-3.1 → "Agree and access"
3. https://huggingface.co/pyannote/segmentation-3.0 → aynı şekilde
4. `.env`:
   ```
   HF_TOKEN=hf_...
   ```

---

## 9. .env tamamlama + KVKK ayarları

`.env` dosyasını gözden geçir. **KVKK hibrit mod için** mutlaka:

```ini
KVKK_HYBRID_MODE=true
NOTION_HIERARCHICAL_MODE=true   # önerilir
WEBHOOK_REQUIRE_SIGNATURE=true
WEBHOOK_PUBLIC_URL=https://klinik.example.com   # HTTPS!
DOCTOR_PHONE=905321234567
GOOGLE_ANAMNESIS_FORM_URL=https://forms.gle/...
GOOGLE_ANAMNESIS_FORM_ID=1FAIpQL...
CLINIC_TZ=Europe/Istanbul
```

---

## 10. Preflight check

```bash
cd /home/clinic/et/clinic
source .venv/bin/activate
python scripts/preflight_check.py
```

Tüm `✗` (kırmızı) hatalar giderilmelidir. `!` (sarı) uyarılar kabul
edilebilir ama gözden geçirin.

---

## 11. Testler

```bash
pytest -v
```

165+ test geçmeli (M1 ML deps yoksa 1 skip).

---

## 12. systemd servisi

`scripts/clinic.service` şablonunu düzenle (path'leri kontrol et) ve kur:

```bash
sudo cp /home/clinic/et/clinic/scripts/clinic.service /etc/systemd/system/clinic.service
sudo systemctl daemon-reload
sudo systemctl enable clinic
sudo systemctl start clinic

# Logları takip et
sudo journalctl -u clinic -f
```

İlk çalıştırma sırasında WhatsApp instance ve Calendar bağlantıları
yapılandırılır.

---

## 13. nginx + Let's Encrypt HTTPS

```bash
sudo cp /home/clinic/et/clinic/scripts/nginx-clinic.conf.example \
        /etc/nginx/sites-available/clinic

# Domain'i düzenle
sudo sed -i 's/<CLINIC_DOMAIN>/klinik.example.com/g' /etc/nginx/sites-available/clinic

# Rate-limit zone'u nginx.conf http{} bloğuna ekle
sudo sed -i '/http {/a\    limit_req_zone $binary_remote_addr zone=admin_login:10m rate=5r/m;' /etc/nginx/nginx.conf

sudo ln -s /etc/nginx/sites-available/clinic /etc/nginx/sites-enabled/clinic
sudo nginx -t
sudo systemctl reload nginx

# HTTPS sertifikası
sudo certbot --nginx -d klinik.example.com
```

---

## 14. Setup modu + Calendar Watch

Evolution webhook + (opsiyonel) Calendar push'u kaydet:

```bash
sudo systemctl stop clinic
sudo -u clinic bash -c 'cd /home/clinic/et/clinic && source .venv/bin/activate && python main.py --setup'
# WhatsApp QR taraması + Calendar Watch aktivasyonu burada yapılır
sudo systemctl start clinic
```

`CALENDAR_PUSH_ENABLED=true` ise push tabanlı bildirim aktif olur;
polling'e ek backup olarak kalır.

---

## 15. İlk akış doğrulama

1. **Admin paneli**: `https://klinik.example.com/ui/login` → `ADMIN_TOKEN` ile giriş
2. **Test hasta kaydı**: `/ui/patients` → form ile kayıt
3. **Test randevu**: Google Calendar'da bir randevu aç (description'da `Tel: 0532XXXXXXX`)
4. Birkaç dakika içinde veliye anamnez WhatsApp'ı gitmeli
5. **Audit log kontrolü**: `/ui/audit` → `patient.create`, `admin_ui.access` event'leri

---

## 16. Yedekleme

Yerel SQLite veritabanları (PII içerir) günlük yedeklenmeli. Cron örneği:

```bash
sudo crontab -e -u clinic
```

```cron
# Her gün 03:00'da DB'leri encrypted tarball'a al
0 3 * * * cd /home/clinic/et/clinic && \
  tar czf /home/clinic/backups/clinic-$(date +\%Y\%m\%d).tar.gz \
    patient_registry.db audit_log.db clinic_state.db \
    credentials.json token.json .env && \
  find /home/clinic/backups -name 'clinic-*.tar.gz' -mtime +30 -delete
```

> Yedekleri offsite'a (KVKK m.12 — yine Türkiye'de) kopyalamak iyi pratik.
> Restic + Vargonen S3 önerilen kombinasyon.

---

## 17. Günlük operasyon

| İhtiyaç | Komut |
|---|---|
| Servis durumu | `sudo systemctl status clinic` |
| Log takibi | `sudo journalctl -u clinic -f` |
| Yeniden başlat | `sudo systemctl restart clinic` |
| Admin paneli | `https://klinik.example.com/ui/` |
| Manuel hatırlatma | Admin UI → Dashboard → Manuel Tetikleyiciler |
| Manuel e-SMM | Admin UI → e-SMM |
| Audit raporu | Admin UI → Denetim |
| Hasta arama | Admin UI → Hastalar (ad veya TCKN) |

---

## 18. Sorun giderme

**WhatsApp bağlantısı kopuyor** → Evolution panelinde QR'ı yeniden tarat.

**Calendar Watch süresi doluyor** → `CalendarWatch` thread'i otomatik
yeniler (saatte bir kontrol). Manuel: `python -c "from calendar_watch import renew_if_needed; renew_if_needed()"`.

**Webhook 401 alıyor** → `WEBHOOK_SECRET` set mi? Evolution panelinde aynı
secret HMAC için ayarlandı mı?

**e-SMM job timeout** → Paraşüt yoğunsa job 30+s sürebilir.
`JOB_POLL_MAX_RETRIES` artırın.

**KVKK denetim talebi** → `/ui/audit` ekranından ilgili tarih aralığı
ile filtreleyip ekran görüntüsü/CSV alın. PII içermez (yalnızca pseudonym).

---

## 19. KVKK gereksinimleri checklist

- [ ] Sunucu Türkiye'de (m.9 yurtdışı aktarım istisnası gerekmez)
- [ ] `KVKK_HYBRID_MODE=true` (Notion'a PII yazılmaz)
- [ ] `PII_ENCRYPTION_KEY` üretildi ve **offline yedeklendi**
- [ ] `.env` izni 600
- [ ] `credentials.json`, `token.json` izni 600
- [ ] HTTPS aktif (Let's Encrypt)
- [ ] Audit log'lar günlük yedekleniyor
- [ ] Hasta velilerinden **yazılı/dijital açık rıza** alınıyor
  (`POST /ui/patients/<uuid>/consent` ile sisteme işleniyor)
- [ ] Veri saklama süresi politikası tanımlı (önerilen: tedavi
  bitiminden 10 yıl, sonra `DELETE /ui/patients/<uuid>`)
- [ ] Personel KVKK farkındalık eğitimi (asistan/sekreter varsa)
- [ ] VERBİS kayıt (gerekiyorsa — yıllık 1M TL ciro üstü zorunlu)
