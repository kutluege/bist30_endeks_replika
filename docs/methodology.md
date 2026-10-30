# BIST 30 (XU030) Metodoloji ve Metadata Özeti

Sahip: Index Methodology Agent · Durum tarihi: 2026-10-30 · Geçerli endeks dönemi: 01.10.2026–31.12.2026

Kaynak kural seti: Borsa İstanbul "BIST Piyasa Değeri Ağırlıklı Pay Endeksleri Kural Seti" (Ağustos 2025),
`kural-seti-son-hali-2025.pdf` / `docs/kural_seti.txt`. Aşağıdaki sayfa numaraları PDF sayfa altbilgisidir.

## 1. Formül ve kurallar (özet)

| Konu | Kural | Kaynak |
|---|---|---|
| Endeks değeri | E_t = Σ (F_it / D_t) · N_it · H_it · K_it / B_t ; XU030 TL için D=1 | Böl. 4, s. 13 |
| Ağırlık katsayısı | Düz XU030 sınırsız → K_i = 1 (sınırlama yalnız "Ağırlık Sınırlamalı" X030C/X030S'de) | 2.6 s. 5; 3. bölüm |
| Pay sayısı N | KAP'taki çıkarılmış/ödenmiş sermaye ÷ 1 TL nominal; tescil öncesi MKK geçici payları dahil | 2.2, s. 5 |
| FDPO H | SPK tanımı, MKK hesaplar; endeks FDPO'su MKK haftalık verisinden **eşikli** güncellenir | 2.5 s. 5; 5.2.f s. 14; 9.2 |
| FDPO hassasiyeti | FDPO ≥ %1 → tam sayı %, < %1 → 2 ondalık | 2.19, s. 7 |
| Bölen hassasiyeti | 8 ondalık; endeks değeri 2 ondalık | 2.19 |
| Bölen düzeltmesi | B_{t+1} = (1 + ΔPD / PD_t) · B_t | 5.1, s. 13 |
| Nakit temettü | Yalnız getiri endeksinde düzeltme (fiyat endeksi bölenine dokunulmaz) | 5.2.a, s. 13 |
| Bedelli (rüçhanlı) | Rüçhan başlangıcında (fiyat ≥ rüçhan fiyatı) ya da süreç bitişini izleyen 4. iş günü | 5.2.b, s. 14 |
| Bedelsiz / bölünme | Teorik fiyat ile P·N sabit → bölen değişmez; N ve fiyat aynı gün değişir | 2.8, 9.1 |
| Endekse alma/çıkarma | Alındığı/çıkarıldığı gün bölen düzeltmesi | 5.2.d–e, s. 14 |
| FDPO değişikliği | MKK'nın haftanın son iş günü verisinde, FDPO ≤ %50 iken ≥ 5 puan, > %50 iken ≥ 10 puan değişim → takip eden haftanın 3. işlem günü; ≤ 2 işlem günlü haftalarda yapılmaz; özsermaye hali düzeltmelerinde MKK son verisi kullanılır | 5.2.f, s. 14 |
| KAP zamanlaması | Bildirim, işlemden önceki iş günü 16:30'a (yarım gün 12:00) kadar KAP'ta olmalı | 5.2, s. 13 |
| Değerleme verisi | BIST 30: Pay Sayısı ve FDPO Kasım/Şubat/Mayıs/Ağustos son günü; hacim 12 ay | 2.16, s. 6 |
| Hesaplama sıklığı | BIST 30 TL fiyat endeksi her saniye (yeni dokümanlarda) | 2.18 |

**Önemli sonuç (5.2.f):** Endekste kullanılan FDPO "yapışkan"dır. MKK'nın güncel oranı ile endeks FDPO'su
arasında, eşik aşılmadıkça, FDPO ≤ %50 için < 5 puan, > %50 için < 10 puan fark olabilir. Bu yüzden
Borsa İstanbul'un KAP'taki "endekslerde kullanılacak FDPO" bildirimleri, varsa, MKK'nın son değerinden önceliklidir.

## 2. Veri kaynakları (tamamı ücretsiz, oturum/paywall yok)

| Veri | Kaynak | Dosya (`data/metadata/raw/`) |
|---|---|---|
| XU030 bileşenleri (resmî) | Borsa İstanbul "Pay Endeks Raporu" https://www.borsaistanbul.com/datum/hisse_endeks_ds.csv (30/10/2026 tarihli) | `hisse_endeks_ds_20261030.csv` |
| Dönemsel değişiklik | Borsa İstanbul duyuru 15598, 21.09.2026: TRMET alındı, DSTKF çıkarıldı (01.10.2026); yedekler BTCIM, TURSG, OYAKC — https://borsaistanbul.com/duyuru/15598/bist-pay-endeksleri-donemsel-degisiklikleri | — |
| N ve MKK FDPO | MKK VAP "Fiili Dolaşım Raporu → Tarih Bazında Tüm Şirketler Raporu" (https://www.vap.org.tr/fiili-dolasim-raporu; açık form, 10 dk'da en çok 5 rapor). ISIN bazında "Toplam Pay Adedi" ve "Fiili Dolaşım Oranı" | `mkk_fdpo/Fiili_Dolasim_Raporu_MKK-{04-09,10-09,11-09,18-09,25-09,02-10,09-10,16-10,23-10}-2026.xlsx` |
| Endeks FDPO bildirimleri | Borsa İstanbul KAP bildirimleri (haber siteleri üzerinden tam listeler; bkz. §4) | — |
| Resmî kapanış fiyatları + XU030 üyelik bayrağı + özsermaye hali bayrağı | Borsa İstanbul günlük pay bülteni https://www.borsaistanbul.com/data/thb/YYYY/MM/thbYYYYMMDD1.zip | `bist_bulletin_thb/` (22.09–28.10.2026) |
| Resmî endeks kapanışları | https://www.borsaistanbul.com/datum/PayEndeksleri.zip (yalnız son gün); geçmiş için Yahoo `XU030.IS` (28.10.2026: 13960.89 = resmî) | `bist_indices/PayEndeksleri_20261028.zip` |
| Dosya dizini | https://www.borsaistanbul.com/files/DataFilePaths.zip | `bist_indices/VerilerDosyaIsimleri.xlsx` |

Bülten "OZSERMAYE HALI" kodları (gözlemle doğrulandı): 6 = nakit temettü (ör. GUBRF 14.10, ASTOR 15.10), 3 = bedelsiz (AYES, KLRHO), 1 = bedelli (SKBNK 22.10).

**Resmî bölen / ağırlık:** Borsa İstanbul'un ücretsiz yayınlarında (PayEndeksleri.zip, hisse_endeks_ds.csv, thb bülteni,
Endeks Verileri sayfası, veri format dokümanı) XU030 bölen değeri ya da bileşen ağırlığı **yok**; bu veri ücretli
Datastore/veri dağıtım kanalında. → Motor bölen değerini **bir kez kalibre etmeli** (`calibrate_divisor`), ardından
kural 5.1 ile düzeltmeli.

## 3. Bileşenler ve alan bazında güven

`data/metadata/constituents.csv` (sütunlar `CONSTITUENT_COLUMNS`), dönem 2026-10-01 – 2026-12-31.
PETKM'in iki satırı var (FDPO değişikliği 28.10.2026).

| Alan | Kaynak | Güven |
|---|---|---|
| Sembol listesi | Borsa İstanbul hisse_endeks_ds.csv (30/10/2026) + bülten "BIST 30 ENDEKS" bayrağı (01–28.10 her gün 30/30 eşleşme) + duyuru 15598 | **Yüksek** (resmî, 3 bağımsız kanıt) |
| N_i | MKK 23.10.2026 ISIN bazında Toplam Pay Adedi. Çok sınıflı şirketlerde yalnız işlem gören sınıf (ISCTR=C, KRDMD=D) — Borsa'nın ayrı sıralarda işlem gören grup paylarına yaklaşımıyla tutarlı | **Yüksek** (resmî kayıt kuruluşu; 22.09–28.10 arasında bileşenlerde bedelsiz/bedelli yok — bülten bayrakları) |
| H_i | Borsa endeks-FDPO bildirimi varsa o (TCELL, SASA, PETKM); yoksa **SPK yöntem değişikliği (11.09.2026) öncesi son MKK haftası 04.09.2026** oranı, 2.19'a göre yuvarlanmış (gerekçe aşağıda) | **Orta**: bildirimli hisseler yüksek; diğerleri kural-temelli tahmin |
| K_i | 1.0 (düz XU030 sınırsız) | **Yüksek** |

## 4. Endeks FDPO bildirimleri (bulunanlar)

| Hisse | Endeks FDPO | Geçerlilik | Kaynak | Kullanım |
|---|---|---|---|---|
| TCELL | %39 | 16.09.2026 | Borsa KAP bildirimi 15.09.2026 (33 pay) — borsamatik.com.tr haber 180784 | kullanıldı (MKK 37,65) |
| SASA | %44 | 16.07.2026 | Borsa KAP bildirimi 14.07.2026 (7 pay) — hisse.net haber 84775 | kullanıldı (MKK 45,15) |
| PETKM | %37 | 28.10.2026 | Borsa KAP bildirimi 27.10.2026 (9 pay) — yatirimx.com.tr haber 27244 | kullanıldı; öncesi %48 **tahmin** |
| ASTOR | %37 | 01.10.2025 | rotaborsa.com/?p=363245 | **reddedildi**: MKK 42,7 (eşik üstü fark → sonraki bildirim olmalı); %37 ima edilen bölen oynaklığını 3,8→6,7 bp'e çıkarıyor. MKK %43 kullanıldı |
| DSTKF | %14 | 23.09.2026 | borsamatik haber 180933 | önceki dönem (artık endekste değil) |

Haziran–Ekim 2026 haftalık bildirimlerinin tamamı tarandı (2, 9, 16 Haz; 14, 28 Tem; 4, 11, 18 Ağu; 1, 8, 15, 22, 29 Eyl;
6, 13, 20, 27 Eki). Diğer 25 bileşen için 2024'ten bu yana bildirim bulunamadı. SPK'nın 03.09.2026 tarihli (54/1626)
ilke kararıyla MKK yöntemi 11.09.2026'dan itibaren değişti; Borsa'nın tüm FDPO'ları topluca yenilediğine dair
bir kanıt yok (15.09 bildirimi yalnız eşiği aşanları içeriyor). KAP doğrudan sorgulanamadı (JS uygulaması).


### 4a. Neden 04.09.2026 MKK değeri? (orchestrator takibi, 30.10.2026)

Kural 5.2.f.i'ye göre endeks FDPO'su yalnız MKK haftalık oranı eşik kadar (≤%50: 5 puan, >%50: 10 puan) değiştiğinde
güncellenir. SPK'nın 03.09.2026 tarih 54/1626 ilke kararıyla MKK 11.09.2026'dan itibaren FDPO'yu yeni yöntemle
hesaplıyor; bileşenlerde bu kayma eşik altında kaldı (ör. TRALT 29,47→25,32; ISCTR 32,15→30,66→29,29; VAKBN 7,44→5,78;
SISE 47,30→44,99; MGROS 50,72→49,11) ve 15.09–27.10 haftalık bildirimlerinde bu hisseler yok. Dolayısıyla endeks
bu hisselerde yöntem değişikliği öncesi FDPO'yu kullanmaya devam ediyor olmalı; en iyi vekil 04.09.2026 MKK oranı.
TCELL örneği (04.09'da MKK 40,25 iken 15.09'da %39'a sıfırlandı) eşiğin haftalık değişime değil **mevcut endeks
FDPO'suna göre** ölçüldüğünü gösteriyor; tahminlerimiz 11.09–23.10 arası her hafta eşik bandında kalıyor
(`test_ff_estimates_respect_rule_5_2_f_threshold`). Değişen 13 hisse: BIMAS .68→.69, EREGL .46→.47, FROTO .17→.18,
GUBRF .20→.21, ISCTR .29→.32, KRDMD .92→.93, MGROS .49→.51, SAHOL .51→.52, SISE .45→.47, TAVHL .49→.50, THYAO .49→.50,
TRALT .25→.29, VAKBN .06→.07. Teşhis (resmî seriye uydurma değil): 01–27.10 günlük std 3,65→3,18 bp,
sürüklenme 8,7→7,3 bp, gün içi (21–27.10) std 2,1→1,1 bp. Kural 5.2.f.i haftalık kontrol (25.09–23.10): yalnız
PETKM eşiği aştı (47,33→37,32) → 28.10 bildirimi ile uyumlu. Ekim bildirimlerinde (06, 13, 20, 27.10) başka bileşen
yok; Ekim'de bileşenlerde sermaye değişikliği yok. MKK 30.10 raporu henüz yayımlanmadı (TRALT eşiğe yakın: 29 vs 24,64).

## 5. Çapraz kontrol: ağırlıklar ve ima edilen bölen

`python -m bist30.metadata.check` → `data/metadata/implied_divisor_check.csv`
(resmî bülten kapanışları × N × H ÷ Yahoo XU030 kapanışı; her gün bülten "BIST 30" bayrağı = dosyamızdaki 30 hisse).

| Dönem | Gün | Ortalama ima edilen bölen | Std | Maks/min |
|---|---|---|---|---|
| 01–27.10.2026 | 19 | 193.573.816 | 3,18 bp | 10,4 bp (01.10→23.10 sürüklenme +7,3 bp) |
| 28.10.2026 | 1 | 193.220.903 | — | −20,2 bp sıçrama = PETKM FDPO %48→%37 (Borsa'nın 28.10 bölen düzeltmesi; beklenen ≈ −18 bp) |

Gün içi (Yahoo 1 dk, 5 dk örnekleme, 21–30.10): ima edilen bölen gün içi std ≈ 1,2 bp; 30 hisse için
ridge regresyonuyla tahmin edilen ölçek çarpanları 0,99–1,01 aralığında → N veya H'de büyük hata işareti yok.
±1–3 puanlık FDPO duyarlılık testinde hiçbir hisse hem günlük hem gün içi testte belirgin iyileşme vermedi
(en iyi tek değişiklik < 1,2 bp). Seviye hataları bölen kalibrasyonunda emilir; yalnız göreli performansla
oluşan sürüklenme kalır (01.10→23.10 ≈ +7,3 bp, endeks −9%; 23.10 FDPO seti ile +8,7 bp idi).

Ağırlıklar (28.10.2026 kapanış): ASELS 15,76 · TUPRS 12,42 · BIMAS 11,20 · THYAO 6,46 · AKBNK 6,44 · KCHOL 4,88 ·
YKBNK 4,21 · EREGL 3,81 · ISCTR 3,59 · SAHOL 3,37 · … · PETKM 0,61 (%). Ücretsiz resmî ağırlık listesi bulunamadı
(GCM Yatırım'ın BIST 100 ağırlık bülteni kurumsal DNS filtresine takıldı).

## 6. Kurumsal işlemler (`data/metadata/corporate_actions.csv`)

* 01.10.2026: TRMET dahil, DSTKF çıkarıldı (dönemsel; bölen düzeltmesi 5.2.d–e).
* 28.10.2026: PETKM FDPO %37 (fiyat endeksi bölenini etkiler).
* Nakit temettü (fiyat endeksi bölenini **etkilemez**): AEFES 05.10, TRALT 06.10, GUBRF 14.10 (brüt 4,4910179 TL),
  ASTOR 15.10 (brüt 2,1911471 TL); ayrıca dönem öncesi TAVHL 22.09, TUPRS 30.09. Kaynak: bülten OZSERMAYE HALI=6.
* 22.09–28.10 arasında bileşenlerde bedelsiz/bedelli/bölünme yok (bülten bayrakları). SPK onayı bekleyen 32 bedelsiz
  başvurusunda (26.09.2026 itibarıyla, hisse.net) ve Ekim bedelli onaylarında (yalnız SKBNK) bileşen yok.
* Kasım–Aralık 2026 için bilinen bölen etkili işlem yok. Takvim: 30.11.2026 değerleme günü; Ocak–Mart 2027 dönemi
  ilanı en az 10 takvim günü önce (≈ Aralık ortası); haftalık FDPO bildirimleri izlenmeli.

## 7. Açık konular

1. **Bildirimi bulunmayan 26 hissenin endeks FDPO'su** MKK 04.09.2026 tahminidir; gerçek değer, son sıfırlamadan bu yana
   her haftalık MKK değerine eşik altı uzaklıkta olmak zorundadır (test edildi). Kalan +7 bp/3 hafta sürüklenme
   bankalar/holdingler (göreli +7..+11%) ile ASTOR/SASA (−10..−14%) arasındaki ayrışmayla eşdoğrusal; tek hisseye
   indirgenemiyor, gerekli düzeltmeler (ör. KCHOL 26→14) makul değil → açık sınırlama.
2. **ASTOR**: bulunan son bildirim (%37) veriyle çelişiyor; %43 kullanıldı. KAP'tan teyit edilmeli.
3. **PETKM 01–27.10 FDPO'su %48 tahmindir** (yalnız geçmiş tarihli hesap için önemli).
4. Resmî bölen yok → motor bir kez kalibre etmeli (`calibrate_divisor`). Bizim N/H ile ima edilen değer ≈ 1,932×10⁸
   (28.10 sonrası); bu resmî bölen değil, bizim H setimize özgü kalibrasyon sabitidir.
5. Her hafta: MKK raporu (VAP) + Borsa FDPO bildirimi + bülten özsermaye bayrakları kontrol edilip
   `build.py` içindeki `INDEX_FDPO_NOTICES` güncellenmeli, `python -m bist30.metadata.build` çalıştırılmalı.
6. Ham MKK xlsx okumak için `openpyxl` venv'e kuruldu (pyproject'e eklenmesi orchestrator'a önerilir).

## 8. Kod

* `bist30.metadata.load_constituents(as_of, path)` → 30 `Constituent`; doğrulama: tam 30 aktif sembol, tekrar yok,
  0 < H ≤ 1, N > 0, 0 < K ≤ 1, çakışmayan geçerlilik aralıkları (`MetadataError`).
* `load_corporate_actions(path)`, `constituents_version(path)` (sha256 ilk 16 hane), `load_constituents_frame` (kaynak sütunlarıyla).
* `python -m bist30.metadata.build` (ham dosyalardan CSV üretir), `python -m bist30.metadata.check` (ima edilen bölen/ağırlık).
* Testler: `tests/test_metadata.py`.
