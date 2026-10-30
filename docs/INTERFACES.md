# Ortak Arayüzler ve Dosya Sahipliği (Orchestrator)

Python env: `.venv/Scripts/python.exe` (3.12, pandas 3.1, yfinance 1.7).
Paket: `src/bist30` (editable install, `pip install -e .`). Testler: `.venv/Scripts/python.exe -m pytest`.
Veri sözleşmeleri: `src/bist30/models.py` (yalnız orchestrator değiştirir; ihtiyaç olursa raporla).
Rule set metni: `docs/kural_seti.txt` (PDF: `kural-seti-son-hali-2025.pdf`, Ağustos 2025).

## Sahiplik

| Alan | Sahip | Dosyalar |
|---|---|---|
| Sözleşmeler, CLI, collector, README, config | Orchestrator | `models.py`, `cli.py`, `collector.py`, `config/`, `README.md` |
| Metodoloji / metadata | Index Methodology Agent | `src/bist30/metadata/**`, `data/metadata/**`, `docs/methodology.md`, `tests/test_metadata*.py` |
| Veri adaptörleri | Market Data Agent | `src/bist30/sources/**`, `data/raw/**`, `docs/data_sources.md`, `tests/test_sources*.py` |
| Endeks motoru | Index Engine Agent | `src/bist30/engine/**`, `docs/engine.md`, `tests/test_engine*.py` |
| VİOP & doğrulama | VİOP & Validation Agent | `src/bist30/analysis/**`, `docs/analysis.md`, `tests/test_analysis*.py` |

Başkasının dosyasına yazma. Eksik ihtiyaç → kendi modülünde geçici adapter/shim yaz ve raporunda belirt.

## Fonksiyon arayüzleri (her ajan bunları SAĞLAR)

### metadata (`bist30.metadata`)
```python
load_constituents(as_of: date | None = None, path=None) -> list[Constituent]     # aktif 30 hisse
load_corporate_actions(path=None) -> pd.DataFrame                                # CORPORATE_ACTION_COLUMNS
constituents_version(path=None) -> str                                           # dosya hash'i
```

### sources (`bist30.sources`)
```python
class Source(Protocol):
    name: str
    latency_class: LatencyClass
    def fetch_bars(self, symbols: list[str], start: datetime, end: datetime, interval="1m") -> pd.DataFrame  # BAR_COLUMNS
    def fetch_quotes(self, symbols: list[str]) -> list[Quote]                                              # opsiyonel
get_source(name: str) -> Source
# Canonical semboller: hisse "AKBNK", endeks "XU030", vadeli "F_XU0301226". Adapter ticker çevirisini kendi yapar.
# Depolama: bist30.sources.store.append_bars(df) / read_bars(symbols, start, end, source=None, latency_class=None)
#   -> data/db/market.sqlite (veya parquet). Duplicates (symbol, market_ts, source) tekilleştirilir.
```

### engine (`bist30.engine`)
```python
compute_index_series(bars: pd.DataFrame, constituents: list[Constituent], divisor_state: DivisorState,
                     official: pd.DataFrame | None = None, grid: str = "1min",
                     staleness_s: float = 300) -> pd.DataFrame        # INDEX_RESULT_COLUMNS
calibrate_divisor(bars, constituents, official, reference_ts) -> DivisorState    # BİR kez
load_divisor_state() / save_divisor_state(state)                                 # data/state/divisor.json
adjust_divisor(state, delta_mcap, mcap_t, reason, effective_date) -> DivisorState # kural 5.1
```

### analysis (`bist30.analysis`)
```python
theoretical_futures(spot: pd.Series, contract: FuturesContract, rate: float|pd.Series, div_yield: float = 0) -> pd.Series
basis_frame(index_result: pd.DataFrame, futures_bars: pd.DataFrame, contract, rate) -> pd.DataFrame
replication_metrics(index_result) -> dict    # MAE, RMSE, max abs, bias, %valid, staleness, latency
plot_intraday(index_result, out_path) / plot_basis(...)
orderbook_features(snapshots) -> pd.DataFrame  # imbalance, microprice (yalnız derinlik verisi varsa)
```

## Kurallar
- Gerçek zamanlı / 15 dk gecikmeli / EOD veri asla aynı hesaplamada karışmaz (`latency_class`).
- `market_ts` (borsa zamanı, bar sonu) ve `received_ts` (bize ulaşma zamanı) ayrı saklanır.
- Bölen bir kez kalibre edilir; resmi değere sürekli uydurma yapılmaz.
- Sahte sonuç üretme. Bir şey çalışmıyorsa açıkça raporla.
- Ücretli abonelik, erişim kısıtı aşma, login atlatma, emir gönderme YOK.
