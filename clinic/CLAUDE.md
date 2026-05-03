# Klinik Yönetim Sistemi — Proje Bağlamı

> Bu dosya Claude'un her oturumda projeyi hatırlamasını sağlar.
> Kararlar değiştiğinde güncelle.

## Proje Özeti

Çocuk ve Ergen Psikiyatrisi muayenehanesi için Python tabanlı otomasyon sistemi.
Modüler monolit, 5 ana modül + main.py orkestratör + admin web UI.

```
[Ses Kaydı] → M1 (Whisper+PyAnnote+Ollama) → SOAP JSON
                                                ↓
[Google Forms anamnez] → M2 (Notion hibrit + yerel PII) → arşiv
                                                ↓
[Google Calendar] → M3 (Evolution API) → WhatsApp randevu/iletişim
                                                ↓
[Tahsilat] → M4 (Paraşüt v4) → e-SMM PDF → WhatsApp
```

## Domain Bilgileri (KESİN)

- **Klinik tipi:** Çocuk ve Ergen Psikiyatrisi (özel muayenehane, tek doktor)
- **Hasta verisi:** KVKK 6. madde — **özel nitelikli kişisel veri** (sağlık + reşit olmayan = en yüksek hassasiyet)
- **Yasal sorumlu:** Doktor (veri sorumlusu)
- **Coğrafya:** Türkiye, randevu/dosya saatleri Europe/Istanbul
- **Mali çerçeve:** Serbest meslek erbabı → e-SMM (gelir vergisi stopajı %20, KDV %0 — mali müşavir teyidi gerekli)

## Verilmiş Mimari Kararlar

| Karar | Seçim | Tarih / Branch |
|-------|-------|----------------|
| Veri yerelleştirme | **KVKK Hibrit** — PII (TCKN, ad, telefon) yerel SQLite + şifreli; Notion'a sadece pseudonym (`Hasta_42`) ve klinik notlar | codex/clinic-system-hardening |
| Para tipi | `decimal.Decimal` (float yasak) | Faz 0 |
| Vergi oranları | `.env` → `STOPAJ_RATE`, `KDV_RATE` (varsayılan 0.20, 0) | Faz 0 |
| Saat dilimi | `CLINIC_TZ=Europe/Istanbul`, tüm karşılaştırmalar aware datetime | Faz 0 |
| Idempotency | SQLite `state_store` — `processed_events`, `processed_audio`, `archived_soaps`, `esmm_records` | Faz 0 |
| Retry | `tenacity` — 5 deneme, exponential backoff (Notion 429, Evo 5xx, Paraşüt) | Faz 1 |
| Logging | `RotatingFileHandler` + PII regex maskeleme (telefon/TCKN/VKN) | Faz 1 |
| Test | pytest + GitHub Actions (Py 3.11/3.12) | Faz 1 |
| Webhook güvenliği | Default fail-closed; `WEBHOOK_REQUIRE_SIGNATURE=false` ile dev için açılır | Faz 0 |
| Tahsilat tetikleyici | POS/payment webhook → otomatik e-SMM | Faz 3 |
| Hatırlatma | 24h + 1h öncesi otomatik | Faz 3 |
| Risk alarmı | SOAP `risk_assessment` taraması → doktora WhatsApp push | Faz 3 |
| Calendar | Watch API (push) — polling fallback | Faz 3 |
| Audit log | KVKK m.12 uyarınca her erişim loglanır | Faz 2 |

## Bekleyen Kararlar / Aksiyonlar

- [ ] **PR #5 merge** (`codex/clinic-system-hardening` → `main`) — temiz merge mümkün, çakışma yok
- [ ] **Türkiye VPS sağlayıcı seçimi** — KVKK için ABD/AB sunucu yasak (sağlık verisi)
- [ ] **Mali müşavir teyidi** — `STOPAJ_RATE`, `KDV_RATE` doğrulaması (her doktor durumuna göre değişir)
- [ ] **KVKK hukuk uzmanı** — aydınlatma metni + açık rıza formu + VERBİS kaydı
- [ ] **Domain + TLS** — webhook public URL için (Cloudflare Tunnel veya nginx + Let's Encrypt)

## VPS Önerisi (KVKK uyumlu, Türkiye konumlu)

| Sağlayıcı | Konum | KVKK | ISO 27001 | Tipik Klinik Plan | Aylık (~TL) |
|-----------|-------|------|-----------|-------------------|-------------|
| **Vargonen** | İzmir, İstanbul | ✅ | ✅ | 2 vCPU / 4GB / 80GB | 250-400 |
| **hosting.com.tr** | İstanbul | ✅ DPA | ⚠️ sadece DC | 2 vCPU / 4GB / 80GB | bakılmalı |
| **Turkcell Bulut** | Türkiye | ✅ | ✅ | 2 vCPU / 4GB / 80GB | 350-600 |
| **TT Bulut** | Türkiye | ✅ | ✅ | 2 vCPU / 4GB / 80GB | 300-500 |
| **DorukNet** | İstanbul | ✅ | ✅ | 2 vCPU / 4GB / 80GB | 400-700 |
| Hetzner (Helsinki) | AB | ⚠️ açık rıza şart | ✅ | 2 vCPU / 4GB / 40GB | ~120 |
| AWS / GCP / Azure | ABD/AB | ❌ sağlık verisi için | — | — | — |

### hosting.com.tr — Detaylı Notlar (yanıt 2026-05-04)
- ✅ İstanbul lokasyon, DPA imzalanabiliyor
- ✅ Disk şifreleme (BitLocker/LUKS) — anahtar müşteride
- ✅ Yedek Türkiye + şifreli, 7 gün saklama
- ✅ 30 gün içinde kalıcı silme + yazılı beyan
- ✅ Güvenlik olayı bildirimi 10-90 dk
- ✅ Erişim rol-bazlı, veri merkezi ISO 27001/27017/27018
- ❌ Firma direkt ISO 27001 yok (sadece DC) — KVKK denetiminde zayıf
- ❌ DR site yok, SLA taahhüdü yok
- ❌ Yedek test kayıtları paylaşılmıyor
- ❌ VERBİS kayıtlı değil (planlama aşamasında)
- KDV %20, e-fatura/e-arşiv, yıllık ödemede 15 gün koşulsuz iade
- VDS (VMware) kurumsal, VPS (KVM) esnek
- URL: hosting.com.tr/server/vds-server/  veya /vps-server/

**Önerim:** Vargonen önde (firma ISO 27001), hosting.com.tr ikincil seçenek
(fiyat avantajı varsa kıyaslanabilir). Karar için Vargonen'den DPA + fiyat
teklifi istenmeli.

**Önerim:** Vargonen — küçük klinik için iyi destek + KVKK belgesi + makul fiyat.
Hetzner sadece gerçekten bütçe darsa ve hukuk uzmanı **özel açık rıza** taslağını onaylarsa.

## Kod Konvansiyonları

- Python 3.11+
- Para: `Decimal` (asla float)
- Tarih: `datetime` + `tzinfo` (asla naive)
- Loglama: `logging_setup.configure_logging()` — modül başında değil, app entry'sinde
- Konfig: `os.getenv` (modül seviyesinde okunabilir; pydantic-settings'e geçiş bekliyor)
- HTTP: `http_retry.create_session()` — tenacity-tabanlı
- State: `state_store.StateStore` — SQLite, idempotency için tek kaynak

## Önemli Dosyalar (codex/clinic-system-hardening branch'inde)

| Dosya | Sorumluluk |
|-------|------------|
| `main.py` | Orkestratör — 3 thread (Audio, Calendar, Webhook) |
| `module1_transcription_engine.py` | Whisper + PyAnnote + Ollama → SOAP JSON |
| `module2_notion_archiver.py` | Pseudonym Notion + yerel PII (KVKK hibrit) |
| `module3_whatsapp_communicator.py` | Evolution API + webhook + hatırlatma |
| `module4_esmm_generator.py` | Paraşüt e-SMM + collection kaydı |
| `module5_migration.py` | Samsung Notes → Notion (tek seferlik) |
| `state_store.py` | SQLite idempotency + audit log |
| `http_retry.py` | tenacity-tabanlı retry helper |
| `logging_setup.py` | Rotating log + PII maskeleme |
| `phone_utils.py` | Telefon normalize (M3+M4 ortak) |
| `patient_store.py` | KVKK yerel hasta deposu (şifreli) |
| `medication_tracker.py` | İlaç takibi |
| `risk_detector.py` | SOAP risk taraması |
| `admin_panel.py` | Flask admin UI |
| `tests/` | pytest paketi (38+ test) |
| `DEPLOYMENT.md` | Production kurulum kılavuzu |

## Hata Bildirim Politikası

Kritik hatalar (Whisper crash, Paraşüt token expiry, WhatsApp instance kopuşu) →
`DOCTOR_PHONE`'a otomatik WhatsApp alarm. Sentry/Glitchtip opsiyonel.

## Çoklu Oturum Hafıza Notu

Önceki Claude.ai oturumunda Faz 0-1-2-3 tamamlandı. Konuşma URL: `claude.ai/code/session_01CBZ8hUcXzhru1R42spBtVm` (yerel olarak erişilemez).
Bu dosya o oturumdan çıkarılan kararların özetidir.
