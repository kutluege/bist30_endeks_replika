# BIST 30 Bağımsız Endeks Hesaplama ve VİOP Analizi

XU030'u Borsa İstanbul kural setine (Ağustos 2025, `kural-seti-son-hali-2025.pdf`) göre ücretsiz verilerle bağımsız hesaplar, resmi endeksle **aynı piyasa zamanında** karşılaştırır ve VİOP BIST 30 vadeli kontratlarının basis/carry analizini yapar.

## Raporu görüntüleme
`reports/xu030_replikasyon.html` tek başına çalışan bir dosyadır: veri, CSS ve JS içine gömülüdür, internet gerekmez.
Başka bir bilgisayarda açmak için dosyayı indirip tarayıcıda açmanız yeterlidir (GitHub'da "Download raw file").

## Kurulum
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
```

## Kullanım
```bash
bist30 collect        # Yahoo 1m/1d + BIST resmi gün sonu verisini SQLite'a doldurur (scripts/backfill.py)
bist30 run            # bölen kalibrasyonu (bir kez) → EOD + 1 dk backtest → VİOP basis → rapor + grafikler
bist30 html           # animasyonlu HTML raporu (reports/xu030_replikasyon.html)
python scripts/poll_live.py   # piyasa saatinde canlı toplayıcı (Yahoo 60 sn, Foreks VİOP 20 sn), 18:35'te kendiliğinden durur
python -m pytest              # testler

Not: Ham indirmeler (`data/db/`, `data/raw/`, `data/metadata/raw/`) repoda tutulmaz, `bist30 collect` ile yeniden indirilir.
Repoda yalnız türetilmiş metadata, sonuçlar, raporlar ve test örnek dosyaları bulunur.
```

## Yöntem (özet)
- **Formül** (Kural Seti md. 4): `E_t = Σ P_i·N_i·H_i·K_i / B_t`. Sade XU030'da ağırlık sınırı olmadığı için K=1.
- **Metadata:** Bileşenler `hisse_endeks_ds.csv`'den alınır (BIST resmi). N, MKK Fiili Dolaşım Raporu'ndan gelir. H (FDPO), MKK değerine kural 2.19 yuvarlaması ve 5.2.f eşiği uygulanarak bulunur. BIST duyurusu varsa duyurudaki değer kullanılır. Ayrıntı: `docs/methodology.md`.
- **Bölen:** Resmi bölen ücretsiz yayımlanmıyor. Bu yüzden dönem başında (01.10.2026 resmi kapanış) **bir kez** kalibre edilir. Sonra yalnız kural 5.1 ile düzeltilir; örneğin 28.10 PETKM FDPO değişikliği −18 baz puan. Resmi seriye yeniden uydurma yapılmaz.
- **Zaman:** Her kayıtta `market_ts` (borsa zamanı, bar sonu) ve `received_ts` (bize ulaşma zamanı) ayrı tutulur. Gerçek zamanlı, 15 dakika gecikmeli ve gün sonu verisi aynı hesaplamada karışmaz (`LatencyMixError`).
- **Geçerlilik:** Eksik fiyat, 300 saniyeden eski fiyat, fiyat limiti dışı, ±%10 sıçrama ve seans dışı gözlemler geçersiz sayılır ve sebebi kaydedilir. Resmi değer yalnız **aynı** `market_ts` ile eşleştirilir.
- **VİOP:** Teorik fiyat `F = S(1+rτ) − temettüler` (ACT/365, TLREF ile). Basis, carry-adjusted residual ve örtük repo hesaplanır. Ayrıntı: `src/bist30/analysis/contracts.py` docstring.

## Proje yapısı
| Yol | İçerik |
|---|---|
| `src/bist30/models.py` | ortak veri sözleşmeleri |
| `src/bist30/metadata/` | bileşen / N / FDPO / kurumsal olay yükleyici ve derleyici |
| `src/bist30/sources/` | Yahoo, Borsa İstanbul (EOD), Foreks (VİOP), TLREF adaptörleri ve SQLite deposu |
| `src/bist30/engine/` | as-of senkronizasyon, geçerlilik, bölen kalibrasyonu ve düzeltme, akış motoru |
| `src/bist30/analysis/` | kontrat özellikleri, adil değer, basis, metrikler, lead-lag, order-book, grafikler |
| `src/bist30/pipeline.py`, `cli.py` | uçtan uca orkestrasyon |
| `data/results/` | `eod_backtest.csv`, `backtest_index_1m.csv`, `run_summary.json` |
| `reports/` | `summary.md`, `figures/`, `xu030_replikasyon.html` |
| `docs/` | `INTERFACES.md`, `methodology.md`, `engine.md`, `data_sources.md` |

## Veri kaynakları ve gecikme
| Kaynak | Kullanım | Gecikme sınıfı | Ölçülen gecikme |
|---|---|---|---|
| Yahoo Finance | hisse + XU030.IS, 1 dk bar (~29 gün geçmiş), 1g kapanış | delayed_15m | medyan ~880 sn |
| Borsa İstanbul datum/bülten | resmi kapanış, bileşen listesi, VİOP uzlaşma | eod | – |
| Foreks.com | VİOP XU030 son / alış / satış anlık görüntüleri | delayed_15m | medyan ~1040 sn |
| Borsa İstanbul TLREF | taşıma faizi | eod | – |

Gerçek zamanlı ücretsiz kaynak ve derinlik verisi bulunamadı. TradingView ve İş Yatırım kurum ağında engelli. investing.com Cloudflare arkasında olduğu için aşılmadı. Bigpara'nın robots.txt dosyası ajanlara izin vermiyor.

## Sonuçlar
Güncel sayılar `reports/summary.md` ve HTML raporunda.

## Bilinen sınırlamalar
- Yahoo 1 dakikalık veride kapanış seansı fiyatı yok (son bar 17:59). Gün sonu karşılaştırması günlük kapanışla yapılır.
- 25 hissenin FDPO'su BIST duyurusu yerine MKK verisinden türetildi (orta güven). Ekim ortasından sonra kalan +5…+10 puanlık kayma büyük olasılıkla buradan geliyor ve açık sınırlama olarak raporlanıyor.
- Lead-lag, fiyat keşfi ve order-book deneylerinin altyapısı hazır ve testli. Gerçek zamanlı veri ve derinlik verisi olmadan çalıştırılmaz; sebebiyle birlikte "yapılamaz" olarak raporlanır.
