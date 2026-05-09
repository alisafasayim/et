# VPS Sağlayıcı Teklif İsteği — Mail Taslağı

**Kullanım:** Aşağıdaki taslağı her sağlayıcıya kopyala-yapıştır.
Sadece **`{SAĞLAYICI_ADI}`** yer tutucusunu değiştirin.

**Gönderilecek 3 sağlayıcı (öneri sırası):**
1. **Radore** → bilgi@radore.com / sales@radore.com
2. **NetInternet** → satis@netinternet.com.tr / iletisim@netinternet.com.tr
3. **DorukNet** → satis@doruk.net.tr / info@doruk.net.tr

**Opsiyonel 4. sağlayıcı:**
- **Bulutistan** → satis@bulutistan.com (sağlık özel paket için)

---

## KONU SATIRI

```
KVKK Uyumlu Sağlık Verisi Hosting — Tüzel Kişi VPS Teklif Talebi
```

---

## MAIL GÖVDESI (kopyala-yapıştır)

```
{SAĞLAYICI_ADI} Satış Ekibi,

Çocuk ve Ergen Psikiyatrisi alanında faaliyet gösteren özel
muayenehanem için KVKK uyumlu bir VPS / VDS sağlayıcısı arıyorum.
İşlediğim veri KVKK 6. madde kapsamında özel nitelikli kişisel
veridir (sağlık + reşit olmayan), bu nedenle teknik ve hukuki
gereksinimler kritik.

Aşağıdaki 17 başlık altında bilgilendirme rica ederim. Mümkünse
2 vCPU / 4 GB RAM / 80 GB Disk konfigürasyonu için aylık ve yıllık
ücret bilgilerini de paylaşabilirsiniz.


1. SUNUCU LOKASYONU
   - Sunucu lokasyonunuz neresi? Türkiye dışında lokasyon
     seçenekleri var mı?

2. KVKK UYUMLULUĞU & VERİ İŞLEME SÖZLEŞMESİ (DPA)
   - Sağlık verisi (KVKK m.6) için Veri İşleme Sözleşmesi (DPA)
     imzalıyor musunuz? Standart bir DPA örneği paylaşabilir
     misiniz?

3. VERBİS DURUMU
   - Şirketinizin VERBİS kaydı bulunuyor mu?

4. VERİNİN YURTDIŞINA ÇIKIŞI
   - Veriler hizmet kapsamında yurtdışına çıkıyor mu? CDN, dış
     yedekleme veya altyapı bileşeni nedeniyle dolaylı transfer
     riski var mı?

5. SERTİFİKASYON
   - Şirketiniz veya altyapınız hangi ISO sertifikalarına sahip
     (ISO 27001, 27017, 27018)? Sertifika kapsamı (firma direkt
     mi, sadece veri merkezi mi)?

6. DİSK ŞİFRELEME
   - Disk şifreleme destekleniyor mu? (BitLocker, LUKS, dm-crypt)
   - Anahtar yönetimi müşteride mi sizde mi?

7. YEDEKLEME
   - Yedek konumu, sıklığı, saklama süresi nedir?
   - Yedekler şifreli mi tutuluyor?
   - Ek ücretli mi?

8. HİPERVİZÖR
   - VDS / VPS için kullandığınız hipervizör (VMware ESXi, KVM,
     Hyper-V vb.) nedir?

9. DDoS KORUMA
   - Standart DDoS koruma kapsamınız nedir? Trafik yurtdışına
     yönlendiriliyor mu?

10. ERİŞİM YETKİLERİ
    - Fiziksel ve sistem erişimleri nasıl yönetiliyor (rol bazlı
      yetkilendirme, log kaydı)?
    - Personel sistemime erişebiliyor mu, hangi koşullarda?

11. GÜVENLİK OLAYI BİLDİRİMİ
    - Güvenlik olayı durumunda müşteri bildirimini ne kadar
      sürede yapıyorsunuz? (KVKK Kurul bildirimi 72 saat)

12. RTO (RECOVERY TIME OBJECTIVE)
    - Felaket durumunda geri yükleme hedef süresi nedir?

13. YEDEK TESTLERİ
    - Yedeklerin doğruluğunu ne sıklıkla test ediyorsunuz?
    - Test raporları müşteri ile paylaşılıyor mu?

14. DISASTER RECOVERY (DR)
    - 2. lokasyon (DR site) bulunuyor mu? Aktif-pasif mı,
      aktif-aktif mı?

15. VERİ SİLME (HİZMET SONU)
    - Hizmet sona erdiğinde veriler ne kadar sürede kalıcı olarak
      siliniyor?
    - Yazılı silme beyanı sağlanıyor mu?

16. SLA & DESTEK
    - Standart SLA taahhüdünüz nedir (%99.9, %99.95 vs.)?
    - 7/24 destek var mı? Destek saatleri ve kanalları (telefon,
      ticket, canlı destek)?

17. FATURALAMA & İPTAL
    - Faturalama dönemi (aylık/yıllık) ve KDV durumu?
    - E-fatura / e-arşiv kesiyor musunuz?
    - İptal ve iade koşulları nelerdir?


SOMUT TEKLİF TALEBİ
-------------------
Yukarıdaki başlıklara ek olarak, aşağıdaki konfigürasyon için
aylık ve yıllık ücret bilgisi rica ederim:

- 2 vCPU / 4 GB RAM / 80 GB SSD Disk
- Türkiye lokasyonu (sertifikalı bir veri merkezinde)
- Disk şifreleme aktif (anahtar bende)
- Yedekleme dahil (şifreli, Türkiye lokasyon)
- DDoS koruma
- DPA imzalanabilir

Hizmet kullanım amacı: Klinik dosya yönetim sistemi (Python uygulaması),
PostgreSQL veritabanı, periyodik yedekleme. Aylık trafik 50-100 GB
civarı, 7/24 erişilebilirlik gereksinimi var.

Cevaplarınızı bekliyor, ilginiz için şimdiden teşekkür ederim.

Saygılarımla,

[Doktor Adı Soyadı]
[Unvan: Çocuk ve Ergen Psikiyatrisi Uzmanı]
[Muayenehane Adı]
[İletişim: e-posta + telefon]
```

---

## Cevap Geldiğinde Ne Yapılacak?

1. Her sağlayıcının cevabını ayrı bir bölüme yapıştırın (CLAUDE.md
   "VPS Önerisi" bölümünde tablo halinde).
2. Ben (Claude) `hosting.com.tr` cevabıyla yan yana karşılaştırma
   tablosu yapacağım.
3. Karar verdikten sonra DPA örneği isteyip avukat ile incelemek
   son adım — KVKK m.12 sözleşme bağlayıcı.

## Pazarlık Notları

- **Yıllık ödemede %10-20 indirim** çoğunlukla pazarlanır.
- **DPA müzakere edilebilir** — eklenmesini istediğiniz maddeler
  varsa (örn. veri işleme sınırlaması, alt-işleyen onayı) baştan
  belirtin.
- **Migrasyon desteği** ücretsiz isteyebilirsiniz.
- **3 ay deneme** veya **1 ay para iadesi** garantisi sorun.
