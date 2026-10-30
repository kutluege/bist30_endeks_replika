# Veri Kaynakları (Market Data Agent)

Son test: **30.10.2026 Cuma, 13:50–14:10 İstanbul** (piyasa açık; Ekim 2026 VİOP vadesinin son günü).
Ağ notu: Geliştirme ortamındaki ağ filtresi **TradingView** ve **İş Yatırım**
alan adlarını engelliyordu (DNS yönlendirmesi, TLS el sıkışması başarısız). Bu engeli aşmak
için hiçbir şey yapılmadı.

Kod: `src/bist30/sources/` — `get_source("yahoo" | "borsaistanbul" | "foreks")`, `store.py` (SQLite),
`rates.py` (TL kısa faiz). Betikler: `scripts/poll_live.py`, `scripts/backfill.py`.

## Özet tablo

| Kaynak | URL | Çalışıyor mu? | Gecikme sınıfı | Sıklık | Alanlar | Ölçülen gecikme | Kullanım / ToS notu | Kısıtlar |
|---|---|---|---|---|---|---|---|---|
| **Yahoo Finance (yfinance)** – hisseler + **XU030.IS** | query1/2.finance.yahoo.com (yfinance 1.7) | ✅ | `delayed_15m` | 1m bar (dakikalık yoklama) | OHLCV (endekste hacim=0→NaN) | `received_ts − market_ts(bar sonu)` medyan **879 s** (min 868, p95 941; likit hisse ve XU030 aynı) | Kişisel/araştırma kullanımı; Yahoo ToS ticari yeniden dağıtımı yasaklar. Kök URL 429 dönebiliyor, yfinance oturum/crumb ile çalışıyor. | 1m geçmiş ≈ **29 gün** (istek başına ≤7 gün). Kapanış seansı (18:00–18:10) 1m'de **yok**. |
| **Borsa İstanbul – PayEndeksleri.zip** (resmi endeks kapanışı) | https://www.borsaistanbul.com/datum/PayEndeksleri.zip | ✅ | `eod` | günlük (yalnız **son işlem günü**) | XU030 dahil tüm endeksler: kapanış, açılış, en düşük, en yüksek | 30.10 öğlen dosya 28.10 verisini içeriyordu (29.10 tatil) | robots.txt `Allow: /`. Veri yayın sözleşmesi: yeniden dağıtım lisansa tabi; dahili doğrulama için kullanılıyor. | Arşiv yok → her gün toplanmalı (backfill/collector). |
| **Borsa İstanbul – Pay Piyasası günlük bülteni** | `/data/thb/YYYY/MM/thbYYYYMMDD1.zip` | ✅ | `eod` | günlük, arşivli | Her pay: önceki kapanış, açılış, en düşük/yüksek, **kapanış**, kapanış seansı fiyatı, AOF, hacim, kalan en iyi alış/satış, BIST30/100 üyeliği bayrağı | T günü akşamı/T+1 yayın | aynı | Endeks değeri içermez. |
| **Borsa İstanbul – Endeks üyelik raporu** | `/datum/hisse_endeks_ds.csv` | ✅ | `eod` | günlük | endeks kodu → bileşen listesi (30.10.2026 itibarıyla XU030'un 30 hissesi) | – | aynı | Ağırlık/pay sayısı yok. Kopya: `data/raw/borsaistanbul/xu030_membership_latest.csv` |
| **Borsa İstanbul – VİOP günlük (geçici) bülteni** | `/viopdata/viop_gecici_tmp_YYYYMMDD.csv` | ✅ | `eod` | günlük, arşivli | Sözleşme bazında **uzlaşma fiyatı**, önceki uzlaşma, OHLC, kapanış, AOF, hacim | T günü akşamı | aynı | Kesin bülten `viop_YYYYMMDD.csv` 30.10'da HTML döndü (adapter otomatik olarak geçiciye düşer). Açık pozisyon geçici bültende yok. |
| **Borsa İstanbul – TLREF** | `/datum/tlreforani.csv`, `/datum/TLREFORANI_D.zip` (UTF‑16) | ✅ | `eod` | günlük | TLREF (%), tarihsel seri 2018→ | 28.10.2026: **%35,7733** | aynı | – |
| **TCMB 1 hafta repo (politika faizi)** | tcmb.gov.tr …/Merkez+Bankasi+Faiz+Oranlari/1+Hafta+Repo | ✅ | – | karar tarihlerinde | tarih, borç verme oranı | 23.10.2026 kararı: **%36,00** | Kamuya açık sayfa | TCMB EVDS API **kişisel API anahtarı** ister → kullanılmadı (istenirse kullanıcı anahtar almalı). |
| **Foreks.com (ForInvest)** – VİOP XU030 vadelileri | https://www.foreks.com/borsa/vadeli-islemler/ ve `/sembol-detay/VXU030n/...` | ✅ | `delayed_15m` | sayfa SSR önbelleği yüzünden yeni anlık görüntü ≈ **1–3 dk**'da bir (20 s'de bir yoklanıyor) | Liste: son fiyat, değişim, `validFrom`; Detay: **son, alış (bid), satış (ask)**, açılış, yüksek, düşük, önceki kapanış, hacim, `dateModified` | `received_ts − market_ts` medyan **1044 s** (min 900, p95 1293) → 15 dk gecikme + önbellek yaşı | robots.txt HTML sayfalara (ClaudeBot/Claude‑Web dahil) **izin veriyor**, `/api/` yasak → **kullanılmıyor**; yalnız sayfadaki schema.org JSON‑LD okunuyor. | Derinlik (kademe) yok; bid/ask **miktarı yok**. Birden fazla önbellek işçisi eski anlık görüntü döndürebiliyor → poller yalnız market_ts'i daha yeni olanı yazar. Foreks kodları (FXU030‑1/‑2) yuvarlanır; kanonik kod sözleşme adından üretilir (`F_XU0301226`). |
| Bigpara (Hürriyet) VİOP | https://bigpara.hurriyet.com.tr/viop-varant/viop-verileri/ | ⚠️ erişilebilir | – | – | son, değişim, saat | Sayfadaki saatler ~3 saat eski göründü | **robots.txt `anthropic-ai`, `ClaudeBot`, `Claude-Web` için `Disallow: /`** → kullanılmadı | – |
| Uzmanpara (Milliyet) VİOP | https://uzmanpara.milliyet.com.tr/vob-varant/varant-viop-fiyatlari/ | ⚠️ erişilebilir | belirsiz | – | son, yüksek, düşük, hacim, saat | Sayfadaki saat 11:44 (14:03'te) → bayat/önbellekli | Adapter yazılmadı | Zaman damgası güvenilir değil. |
| TradingView scanner / chart | scanner.tradingview.com, data.tradingview.com | ❌ (kurumsal DNS engeli) | – | – | – | – | Engel aşılmadı | Başka ağda denenebilir; ToS otomatik veri çekmeye kısıtlı. |
| İş Yatırım | www.isyatirim.com.tr | ❌ (kurumsal DNS engeli) | – | – | – | – | – | – |
| Investing.com | tr.investing.com | ❌ HTTP 403 (Cloudflare) | – | – | – | – | Cloudflare **aşılmadı** (kural) | – |
| Yahoo VİOP | `F_XU0301226.IS`, `XU0301226.IS`, `XU030D.IS` | ❌ veri yok | – | – | – | – | – | Yahoo VİOP listelemiyor. |
| Doviz.com | borsa.doviz.com/endeksler (spot), /viop 404 | ❌ VİOP sayfası bulunamadı | – | – | – | – | – | – |
| Mynet Finans | finans.mynet.com/viop/ | ❌ 404 | – | – | – | – | – | – |

**Gerçek zamanlı (realtime) ücretsiz ve izinli bir kaynak bulunamadı.** Tüm gün içi veriler
`delayed_15m`; resmi değerler `eod`. Bu iki sınıf aynı hesaplamada karıştırılmamalı.

## Yahoo ayrıntıları ve tuhaflıklar

* Bar etiketi = bar **başlangıcı**, +03:00. Adapter UTC'ye çevirir, `market_ts = bar_start + aralık`.
  Günlük barlarda `market_ts` = o günün 18:10 (İstanbul) kapanış seansı.
* **Gecikme**: tüm likit semboller için aynı (~14,6 dk bar sonundan; yani son bar ~15 dk geriden).
  Poller her dakika `delay_log` tablosuna sembol bazında yazar.
* **Zaman ızgarası**: XU030.IS ve hisseler aynı dakika ızgarasında (saniye=0). Fark: XU030'da
  09:55 bar (açılış = önceki resmi kapanış 13960.89 → açılış seansı değeri) + **09:56–09:59 düz barlar**
  var; hisselerde 09:55 barı (açılış seansı fiyatı, **hacim 0**) var, 09:56–09:59 **yok**, sürekli işlem 10:00'da başlar.
* **Kapanış seansı yok**: 1m seri 17:59 başlangıçlı barla (18:00 bitiş) biter; 18:00–18:10 kapanış
  seansı fiyatı 1m'de görünmez. Örn. 27.10: XU030 1m son = 13880.72, resmi kapanış = 13934.70.
  Yahoo **1d** kapanışları resmi Borsa İstanbul kapanışlarıyla birebir uyumlu (477 karşılaştırma, hepsi < %0,01).
  28.10 (yarım gün) 1m serisi 12:40'ta bitti ve resmi kapanışla eşleşti.
* **Son bar kısmi**: en son bar oluşmaya devam eder (çoğunlukla hacim=0). `store.append_bars` aynı
  (symbol, market_ts, source, interval) için değişen OHLCV'yi günceller; `first_received_ts` ilk görülme anını tutar.
* **Eksik dakikalar**: likit hisselerde medyan 1 dk; az likit isimlerde (SOKM, OTKAR, TABGD...) onlarca dk.
  İşlem olmayan dakika için bar üretilmez (engine forward-fill + staleness uygulamalı).
* KOZAL/KOZAA artık veri döndürmüyor (TRALT/TRMET olarak değişti).

## Depolama (`bist30.sources.store`)

SQLite `data/db/market.sqlite` (WAL). Tablolar:
`bars` (PK symbol, market_ts, source, interval; ek kolon `first_received_ts`),
`quotes` (VİOP anlık görüntüleri, bid/ask; `extra` JSON: açılış/yüksek/düşük/önceki kapanış),
`orderbook` (derinlik — ücretsiz kaynak olmadığı için boş), `delay_log` (ölçülen gecikmeler).
API: `append_bars`, `read_bars(symbols, start, end, source=None, latency_class=None, interval=None)`,
`append_quotes`, `read_quotes(..., dedupe_market_ts=True)`, `append_orderbook`, `read_orderbook`,
`read_delays`, `counts`. VİOP anlık görüntülerinden bar: `bist30.sources.foreks.bars_from_quotes`.

## Poller

`scripts/poll_live.py`: 60 s'de bir Yahoo 1m (72 sembol + XU030/XU100, tek toplu istek), 20 s'de bir
Foreks detay sayfaları (3 XU030 sözleşmesi, istekler arası ≥3 s), 60 s'de bir Foreks liste sayfası.
Log: `logs/poller.log`; PID: `data/state/poller.pid`; ham örnekler 30 dk'da bir `data/raw/live/`.
Varsayılan çıkış **18:35** (talimattaki 18:20 yerine): 15 dk gecikmeli kaynaklarda 18:10 kapanış
seansı ve 18:15 VİOP kapanışı ancak ~18:25–18:30'da görünür. `--until 18:20` ile değiştirilebilir.

## Bilinen sınırlamalar

* Gerçek zamanlı veri yok; derinlik / bid‑ask miktarı yok.
* Resmi XU030 gün içi (dakikalık) değer yalnız Yahoo üzerinden (15 dk gecikmeli); resmi EOD
  dosyası yalnız son günü veriyor (arşiv için her gün toplanmalı).
* VİOP gün içi veri Foreks önbellekli sayfalarından: düzensiz örnekleme (~1–3 dk), 15–22 dk gecikme.
