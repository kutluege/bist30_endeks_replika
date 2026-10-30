# Endeks Motoru (`bist30.engine`)

Sahip: Index Engine Agent. Kod: `src/bist30/engine/` (`core.py`, `divisor.py`, `session.py`, `diagnostics.py`). Testler: `tests/test_engine.py`.

## Formül
Kural seti md. 4 (TL fiyat endeksi, D=1, XU030 için K=1):

    E_t = Σ P_i · N_i · H_i · K_i / B_t

Hassasiyet (md. 2.19): endeks 2 hane, bölen 8 hane. `raw_mcap = Σ P·N·H·K` yuvarlanmaz.

## As-of senkronizasyon
- Grid: varsayılan `1min`, grid noktası = bar sonu. Bir bar `ceil(market_ts, grid)` noktasına atanır; t noktasında yalnız `market_ts ≤ t` olan barlar kullanılır (look-ahead yok).
- Her hisse için son geçerli işlem fiyatı tutulur. `volume > 0` (veya NaN = bilinmiyor) olan bar işlem sayılır. Yahoo’nun sıfır hacimli düz barları "yeni işlem yok" sayılır; sıfır hacimli ama fiyatı değişen bar (ör. 09:55 açılış seansı fiyatı) fiyatı günceller, ancak tazelik (staleness) saatini sıfırlamaz. Bu durum `zero_volume_price_update` bayrağıyla kaydedilir.
- Tazelik: `staleness_i = t − son işlem zamanı`. Ayrıca ağırlıklı tazelik `Σ w_i·staleness_i` ve eskimiş hisselerin ağırlığı `stale_weight` hesaplanır (w = P·N·H·K payı).
- Satır yalnız yeni veri varsa üretilir: (t−grid, t] aralığında en az bir bileşenin yeni barı olmalı (`only_new=True`).
- Akış modu: `IndexEngine.update(new_bars)` aynı kod yolunu kullanır. Grid noktası, daha sonraki bir grid noktasına ait bar geldiğinde veya `flush()` çağrıldığında kesinleşir. Kesinleşmiş satırlar geriye dönük değişmez. Geç gelen barlar yalnız hisse durumunu günceller (`late_bars`). Batch sonuç ile akış sonucu birebir aynıdır (test edildi).

## Geçerlilik kuralları (`valid`, `invalid_reason`, `flags`)
Sert kurallar (`valid=False`, `calc_value=NaN`; bilgi için `calc_value_unchecked` yine doldurulur):
| Neden | Kural |
|---|---|
| `missing_price:SYM` | Bileşenin t anına kadar hiç fiyatı yok |
| `stale>300s:SYM` | Sürekli işlemde son işlem eşikten eski (`staleness_s`, varsayılan 300 s) ve `stale_weight > stale_weight_tolerance` (varsayılan 0) |
| `price_jump>10%:SYM` | Önceki işleme göre ±%10’dan büyük sıçrama (`jump_pct`) |
| `outside_price_limit:SYM` | Önceki seans kapanışına (baz fiyat; kurumsal işlemde düzeltilmiş) göre ±%10 + %0,5 toleransın dışında |
| `outside_session` | Grid noktası seans dışında (hafta sonu, 09:40 öncesi, kapanış sonrası) |
| `divisor_adjustment_failed:...` | Gün başı bölen düzeltmesi yapılamadı (ör. eksik kapanış fiyatı) |

Yumuşak bayraklar (`flags`, satırı geçersiz yapmaz): `auction:opening_auction|closing_auction|closing_trades` (`auction_policy="invalid"` ile serte çevrilebilir), müzayede sırasında eskime (`stale:`), `zero_volume_price_update`, `before_calibration_date`.

Sert hata: farklı `latency_class` değerleri (fiyat barları kendi içinde veya fiyat ile resmi seri arasında) aynı hesaplamaya girerse `LatencyMixError` fırlatılır.

Resmi XU030 değeri yalnız **aynı market_ts** ile eşleştirilir (`attach_official`). O anda resmi değer yoksa `official_value`, `diff_pts` ve `diff_pct` NaN kalır; en yakın zamanla eşleştirme yapılmaz.

## Seans fazları (`session.py`, İstanbul saati, bar sonu semantiği)
09:40–10:00 açılış müzayedesi (Yahoo’da 09:55 barı açılış fiyatını taşır), 10:00–18:00 sürekli işlem, 18:00–18:05 kapanış müzayedesi, 18:05–18:10 kapanış fiyatından işlem. Yarım günde (arife) 12:30 / 12:35 / 12:40. Yarım gün listesi `DEFAULT_HALF_DAYS` içindedir; her yıl BIST takviminden doğrulanmalıdır.
Gözlem: Yahoo 1m verisinde tam günlerde 17:59 barından sonra veri yoktur, yani kapanış müzayedesi fiyatı 1m veride bulunmaz (yalnız günlük barda vardır). Yarım günde (2026-10-28) ise 12:35 barında görünür.

## Bölen: kalibrasyon politikası
- `calibrate_divisor(bars, constituents, official, reference_ts)` **bir kez** çalıştırılır: `B = Σ P·N·H·K (ref) / E_resmi(ref)`, 8 haneye yuvarlanır. Fiyatlar `reference_ts` anındaki as-of değerlerdir. Resmi değer tam olarak `reference_ts` anında bulunmalıdır, aksi halde `CalibrationError` verilir.
- Önerilen referans: önceki günün resmi kapanışı. Hisse ve XU030 için aynı gecikme sınıfındaki günlük kapanış barları kullanılır. Alternatif: aynı 1m kaynağın son sürekli işlem dakikası (18:00).
- Resmi bölen biliniyorsa `official_divisor=` verilir ve `method="official"` olur.
- Kalibrasyondan sonra resmi seriye **asla** yeniden uydurma yapılmaz. Bölen yalnız kural 5.1 ile değişir.
- Kalıcılık: `save_divisor_state()` / `load_divisor_state()` → `data/state/divisor.json`. `adjustments` listesinin ilk girdisi kalibrasyon kaydıdır (kullanılan fiyatlar, tazelik), sonraki girdiler 5.1 düzeltmeleridir.

## Bölen düzeltmesi (kural 5.1 / 5.2)
`adjust_divisor(state, ΔPD, PD_t, reason, effective_date)`: `B_{t+1} = (1 + ΔPD/PD_t)·B_t`, denetim kaydı ile.
`apply_index_events(...)` bir gündeki tüm olayları tek adımda uygular. `PD_t` t günü kapanış fiyatlarıyla eski bileşenlerden, `PD_new` düzeltilmiş fiyatlarla yeni N/H/K’dan hesaplanır.
- Bedelsiz / bölünme: `N' = N(1+r)`, `P' = P/(1+r)` → `ΔPD = 0`, bölen değişmez (test edildi).
- Bedelli (rüçhanlı): `P' = (P + r·S)/(1+r)`, `ΔPD = r·N·S·H·K`.
- Nakit temettü: fiyat endeksinde düzeltme **yok** (md. 5.2.a), yalnız kayıt düşülür.
- FDPO değişimi, endekse giriş/çıkış: kapanış fiyatından `ΔPD`.
- Bileşen listesi dönem değişimi (`effective_from/to`): yalnız dosyada değişen semboller uygulanır. Kurumsal işlemle güncellenmiş N değerleri korunur.
Motor, takvim günü değiştiğinde önceki günün son fiyatlarıyla bu adımı otomatik yapar (`compute_index_series(..., actions=df)`). Güncel durum `df.attrs["divisor_state"]` içindedir.
Not: Bedelsiz veya bölünmeden kaynaklanan N değişimi `corporate_actions` üzerinden gelmelidir (fiyat düzeltmesi gerekir). Bileşen dosyasındaki çıplak N değişimi kapanış fiyatından ΔPD olarak işlenir.

## Resmi kapanışlar (`daily_closes`)
`compute_index_series(..., daily_closes=df)` / `IndexEngine(..., daily_closes=df)`. Biçim: (a) BAR_COLUMNS 1d barları (seans tarihi `bar_start` alanının İstanbul tarihidir; yoksa `date`, o da yoksa `market_ts` kullanılır) veya (b) geniş tablo (index = tarih, sütunlar = semboller). Yeni gün d başladığında d’den önceki en son tarihin resmi kapanışları şu işlerde kullanılır: (1) hissenin son fiyatı, yani ilk işleme kadar geçerli fiyat, (2) fiyat limiti baz fiyatı, (3) kural 5.1 geçişinde PD_t ve ΔPD hesabı. Kapanışı olmayan sembolde son gün içi fiyata dönülür. Hangi bazın kullanıldığı `attrs["base_sources"]` içinde kaydedilir. Hacim yok sayılır (Yahoo 1d SASA hacmini 0 gösterir, kapanış yine geçerlidir). Gecikme sınıfı kontrol edilmez, çünkü bunlar hesaplama fiyatı değil referans kapanışlardır. Gerekçe: Yahoo 1m verisinde kapanış müzayedesi fiyatı yoktur (ör. ASTOR 02.10: son 1m fiyatı 222,50, resmi kapanış 220,00).

## Teşhis
- `lag_diagnostics(result, max_lag=5)`: 1 dakikalık log getirilerin çapraz korelasyonu, `corr(calc_r(t), resmi_r(t+k))`, k=−5..+5. Yalnız teşhis amaçlıdır; seriyi kaydırmak veya uydurmak için kullanılmaz.
- `replication_error_summary(result)`: MAE, RMSE, bias, maksimum mutlak hata, % geçerli satır, tazelik istatistikleri.
