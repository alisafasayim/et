# hosting.com.tr Canlı Destek — Soru Listesi

**Senaryo:** Çocuk ve ergen psikiyatrisi muayenehanesi için VPS arıyorum. Üzerinde hasta verisi (anamnez, klinik notlar, e-SMM kayıtları) tutulacak. KVKK kapsamında **özel nitelikli kişisel veri** (sağlık + reşit olmayan) işleyeceğim.

> **Sıralama önemli:** İlk 5 soru "deal-breaker"dır. Olumsuz cevap gelirse diğerlerine geçmeden başka sağlayıcıya bakın.

---

## 🔴 KRİTİK — Yasal & KVKK (deal-breaker)

1. **Sunucularınız fiziksel olarak Türkiye'de mi konumlu? Hangi şehir, hangi veri merkezi?**
   *(Beklenen: "İstanbul/Ankara/İzmir, kendi/Equinix/Turkcell DC". "Almanya, Hollanda" cevabı = HAYIR.)*

2. **KVKK kapsamında "veri işleyen" sıfatıyla benimle Veri İşleme Sözleşmesi (VİS / DPA) imzalıyor musunuz?**
   *(Sağlık verisi için zorunlu. Standart şablon var mı, yoksa özel mi yazıyoruz?)*

3. **VERBİS'te kayıtlı bir veri işleyensiniz mi? Kayıt numaranızı paylaşır mısınız?**
   *(VERBİS'i sorgulayıp doğrulayacağım.)*

4. **Verim hiçbir koşulda yurtdışına çıkmaz değil mi? CDN, DDoS koruma, yedekleme dahil — hiçbir şey AB/ABD'de saklanmıyor mu?**
   *(Cloudflare gibi yurtdışı hizmet kullanıyorlarsa sorun olabilir.)*

5. **ISO 27001, ISO 27017, ISO 27018 sertifikalarınız var mı? Belge numarası ve denetim tarihi nedir?**
   *(Sağlık verisi için ISO 27001 minimum, 27018 (cloud privacy) idealdir.)*

---

## 🟡 ÖNEMLİ — Güvenlik

6. **Disk şifreleme (LUKS, dm-crypt veya storage seviyesinde) destekleniyor mu? Anahtar yönetimini ben mi yapıyorum, siz mi?**

7. **VPS'imin snapshot/imaj yedeklerini siz alıyor musunuz? Aldığınız yedekler şifreli mi, hangi konumda saklanıyor, ne kadar süre tutuluyor?**

8. **Hipervizör (KVM/Xen/VMware) hangisi? Şifreli RAM (Intel SGX, AMD SEV) destekliyor mu?**
   *(KVM tercih edilir; OpenVZ olmasın çünkü kernel paylaşımlı.)*

9. **DDoS koruması dahil mi, hangi seviyede (Mbps/Gbps), Türkiye'de mi yoksa yurtdışı bir CDN üzerinden mi geçiyor?**

10. **Sunucuya kim fiziksel erişim sağlayabiliyor? Personeliniz benim sanal diskime root erişebilir mi? Hangi durumlarda?**

11. **Güvenlik olayı/saldırı durumunda KVKK m.12 uyarınca kaç saat içinde benimle iletişime geçiyorsunuz?**
    *(KVKK 72 saat içinde Kuruma bildirimi zorunlu kılar — sizin bunu daha hızlı yapmanız lazım.)*

---

## 🟡 ÖNEMLİ — Yedekleme & Felaket Kurtarma

12. **Yedeklerin RPO (kaç saat veri kaybı) ve RTO (kaç saat geri dönüş) süreleri nedir?**

13. **Yedek dönüş testi (restore test) ne sıklıkta yapılıyor? Kayıtları görebilir miyim?**

14. **Veri merkezinin ikinci konumu var mı (DR site)? Şiddetli deprem/yangın senaryosunda veri nereye gider?**

15. **Hizmet sözleşmesi sona erdiğinde verim ne kadar süre içinde kalıcı olarak silinir? Silme sertifikası verebilir misiniz?**
    *(KVKK m.7 — kişisel verinin imhası belgelenmeli.)*

---

## 🟢 TEKNİK — Klinik sistemim için

16. **VPS'de **Docker, systemd, nginx, Python 3.11+, PostgreSQL/SQLite** çalıştırabilir miyim? Root erişim tam mı?**

17. **Plan değişikliği: 2 vCPU / 4 GB RAM ile başlasam, sonra 4 vCPU / 8 GB'a yükseltmek istesem **dakikalar içinde** mümkün mü, yoksa yeni VPS mi açıyoruz?**

18. **Statik IPv4 ve IPv6 adresleri dahil mi? Reverse DNS (PTR) ayarlanabilir mi? Ek IP fiyatı?**

19. **Aylık trafik limiti kaç TB? Aşılırsa ne oluyor (kesilme, ek ücret, throttle)?**

20. **Sunucu üzerinde **WhatsApp Business API webhook (HTTPS, port 443)**, **Google Calendar push notification (HTTPS callback)** ve **Paraşüt API çağrıları (giden HTTPS)** çalışacak. Bu trafik için kısıtlama yok değil mi?**

21. **Ücretsiz Let's Encrypt sertifikası kullanabilir miyim? cPanel/Plesk gibi panel zorunlu mu?**

22. **OS olarak **Ubuntu 22.04 LTS** veya **Debian 12** seçebiliyor muyum? Kurulum şablonunuz güncel mi?**

---

## 🟢 OPERASYONEL — Destek & SLA

23. **Uptime SLA'nız nedir (örn. %99.9 = ayda en fazla 43 dk kesinti)? SLA aşılırsa ne oluyor — para iadesi mi, kredi mi?**

24. **7/24 Türkçe destek var mı? Telefon/canlı sohbet/ticket için yanıt süreleri nedir?**

25. **Acil teknik destek ek ücretli mi? Yönetilen VPS (managed) ile yönetilmemiş arasındaki fark nedir?**

26. **Mevcut "VPS-X" planında destek hangi seviyede dahil — sadece donanım sorunu mu, yoksa OS/uygulama sorunu da mı?**

---

## 🟢 TİCARİ

27. **Aylık/yıllık ödeme farkı? KDV dahil mi, e-fatura/e-arşiv kesiyorsunuz mu?**

28. **Sözleşme minimum süresi var mı? İptal halinde iade politikası nedir? Yıllık ödediğimde aylık iptalde geri alabilir miyim?**

29. **Domain + SSL paketinizle birlikte indirim var mı?**

30. **2 vCPU / 4 GB RAM / 80 GB SSD planınızın **şu anki net aylık fiyatı** nedir? Promosyon süresi bittiğinde fiyat ne olacak?**

---

## ⚖️ Karar Filtresi

Bu cevapları aldıktan sonra **şu kırmızı çizgileri** kontrol edin:

- ❌ "Sunucu Almanya'da" → BAŞKA SAĞLAYICI
- ❌ "DPA imzalamıyoruz" → BAŞKA SAĞLAYICI
- ❌ "ISO 27001 yok" → MUMKUN AMA RİSK; mali müşavir/avukatla konuşun
- ❌ "OpenVZ kullanıyoruz" → BAŞKA PLAN/SAĞLAYICI iste (KVM şart)
- ❌ "Yedekler ABD/AB CDN'inde" → BAŞKA SAĞLAYICI
- ⚠️ "Snapshot manuel ve ücretli" → kabul edilebilir, ama otomasyon için ek script lazım
- ⚠️ "Sözleşme min 1 yıl" → kabul edilebilir, ama ilk ay test için aylık tercih edilir

## 💬 Konuşma Stratejisi

1. **İlk önce 1-5'i sorun.** "Hayır" gelirse zaman kaybetmeyin.
2. Onay alırsanız **6-15'i** sorun (DPA örneğini e-postayla isteyin).
3. **16-22'yi** sorun — teknik gereksinimler.
4. **23-30'u** son aşamada görüşün; pazarlık alanı burası.
5. **DPA + KVKK uyumluluk taahhütnamesini yazılı** isteyin — sözlü vaat KVKK Kurulu nezdinde geçerli değil.

## 📝 Cevapları kaydetmek için

Aşağıdaki tabloyu doldurarak konuşmadan sonra karşılaştırma yapın:

| # | Soru özet | Cevap | Tatmin edici? |
|---|-----------|-------|---------------|
| 1 | Sunucu konumu | | |
| 2 | DPA imza | | |
| 3 | VERBİS no | | |
| 4 | Yurtdışı veri çıkmaz mı | | |
| 5 | ISO 27001/27018 | | |
| 16 | Docker/systemd OK | | |
| 30 | Net aylık fiyat | | |

---

**Not:** "Çocuk psikiyatrisi" demeden "sağlık verisi işliyorum, KVKK özel nitelikli kategori" demeniz yeterli — bazı destek ekipleri spesifik branş duyunca farklı (gereksiz) prosedür önerebilir.
