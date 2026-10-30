"""Yahoo Finance adapter (via yfinance) for BIST equities and the official XU030 index.

Canonical symbols: equities "AKBNK" -> "AKBNK.IS"; index "XU030" -> "XU030.IS".
Yahoo labels intraday bars by bar START in Europe/Istanbul (+03:00). We convert to UTC
and set ``market_ts = bar_start + interval`` (bar END). For daily bars ``market_ts`` is
the session close (18:10 Istanbul) of that day.

Measured on 2026-10-30: intraday bars arrive ~15 minutes after real time
(see docs/data_sources.md) -> ``LatencyClass.DELAYED_15M``.

Quirks handled here:
  * rows with NaN close (missing minutes / ticker with no trades) are dropped;
  * the latest bar may still be forming (partial); callers that poll should upsert
    (``store.append_bars`` updates changed OHLCV of an existing bar);
  * index (XU030.IS) volume is reported as 0 -> stored as NaN;
  * 1m data is limited to ~30 days back and <= 8 days per request -> we chunk;
    if 1m is unavailable we fall back to 2m -> 5m -> 15m -> 1d.
"""

from __future__ import annotations

import logging
import warnings
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from bist30.models import BAR_COLUMNS, InstrumentType, LatencyClass, Quote, empty_bars

log = logging.getLogger(__name__)

INDEX_SYMBOLS = {"XU030", "XU100", "XU050", "XBANK", "XUSIN", "XUTUM"}
INTERVAL_DELTA = {
    "1m": timedelta(minutes=1), "2m": timedelta(minutes=2), "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15), "30m": timedelta(minutes=30), "60m": timedelta(hours=1),
    "1h": timedelta(hours=1), "1d": timedelta(days=1),
}
# max look-back Yahoo serves per interval, and max span per request
MAX_LOOKBACK = {"1m": timedelta(days=29), "2m": timedelta(days=59), "5m": timedelta(days=59),
                "15m": timedelta(days=59), "30m": timedelta(days=59), "60m": timedelta(days=729),
                "1h": timedelta(days=729), "1d": timedelta(days=36500)}
MAX_SPAN = {"1m": timedelta(days=7)}
FALLBACK_CHAIN = ["1m", "2m", "5m", "15m", "1d"]


def _silence():
    try:
        from pandas.errors import Pandas4Warning  # type: ignore
        warnings.filterwarnings("ignore", category=Pandas4Warning)
    except Exception:  # pragma: no cover
        pass
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def to_yahoo(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith(".IS") or s.startswith("^"):
        return s
    return f"{s}.IS"


def from_yahoo(ticker: str) -> str:
    return ticker.upper().removesuffix(".IS")


def instrument_type(symbol: str) -> InstrumentType:
    return InstrumentType.INDEX if symbol.upper() in INDEX_SYMBOLS else InstrumentType.EQUITY


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def normalize_download(raw: pd.DataFrame, symbols: list[str], interval: str,
                       received_ts: datetime, source: str = "yahoo",
                       latency_class: LatencyClass = LatencyClass.DELAYED_15M) -> pd.DataFrame:
    """Convert a ``yf.download(..., group_by='ticker')`` frame to BAR_COLUMNS."""
    if raw is None or len(raw) == 0:
        return empty_bars()
    frames = []
    multi = isinstance(raw.columns, pd.MultiIndex)
    for sym in symbols:
        tk = to_yahoo(sym)
        if multi:
            lvl0 = raw.columns.get_level_values(0)
            if tk in lvl0:
                sub = raw[tk]
            elif tk in raw.columns.get_level_values(1):
                sub = raw.xs(tk, axis=1, level=1)
            else:
                continue
        else:
            if len(symbols) != 1:
                continue
            sub = raw
        sub = sub.rename(columns=str.lower)
        if "close" not in sub.columns:
            continue
        sub = sub[sub["close"].notna()]
        if sub.empty:
            continue
        idx = pd.DatetimeIndex(sub.index)
        if interval == "1d":
            # daily rows are labelled at local midnight; the bar ends at the closing auction
            if idx.tz is None:
                idx = idx.tz_localize("Europe/Istanbul")
            local = idx.tz_convert("Europe/Istanbul").normalize()
            start = local.tz_convert("UTC")
            end = (local + pd.Timedelta(hours=18, minutes=10)).tz_convert("UTC")
        else:
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            start = idx.tz_convert("UTC")
            end = start + INTERVAL_DELTA[interval]
        itype = instrument_type(sym)
        vol = sub["volume"].astype(float) if "volume" in sub.columns else pd.Series(float("nan"), index=sub.index)
        if itype == InstrumentType.INDEX:
            vol = vol.where(vol > 0)
        frames.append(pd.DataFrame({
            "symbol": from_yahoo(sym),
            "instrument_type": itype.value,
            "bar_start": start,
            "market_ts": end,
            "open": sub["open"].astype(float).values,
            "high": sub["high"].astype(float).values,
            "low": sub["low"].astype(float).values,
            "close": sub["close"].astype(float).values,
            "volume": vol.values,
            "source": source,
            "latency_class": latency_class.value,
            "interval": interval,
            "received_ts": _utc(received_ts),
        }))
    if not frames:
        return empty_bars()
    out = pd.concat(frames, ignore_index=True)
    return out[BAR_COLUMNS].sort_values(["symbol", "market_ts"]).reset_index(drop=True)


class YahooSource:
    name = "yahoo"
    latency_class = LatencyClass.DELAYED_15M

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        _silence()

    # -- low level -----------------------------------------------------------------
    def _download(self, tickers: list[str], interval: str, start=None, end=None, period=None) -> pd.DataFrame:
        import yfinance as yf
        _silence()
        kw = dict(interval=interval, group_by="ticker", auto_adjust=False, actions=False,
                  prepost=False, progress=False, threads=True, timeout=self.timeout,
                  multi_level_index=True)
        if period:
            kw["period"] = period
        else:
            kw["start"], kw["end"] = start, end
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return yf.download(tickers, **kw)

    def fetch_bars_once(self, symbols: list[str], interval: str = "1m", start=None, end=None,
                        period: Optional[str] = None) -> pd.DataFrame:
        tickers = [to_yahoo(s) for s in symbols]
        received = datetime.now(timezone.utc)
        raw = self._download(tickers, interval, start=start, end=end, period=period)
        received = datetime.now(timezone.utc)
        return normalize_download(raw, symbols, interval, received, self.name, self.latency_class)

    # -- protocol ------------------------------------------------------------------
    def fetch_bars(self, symbols: list[str], start: datetime, end: datetime, interval: str = "1m",
                   fallback: bool = True) -> pd.DataFrame:
        """Bars with market_ts in [start, end]. Falls back to coarser intervals if empty."""
        now = datetime.now(timezone.utc)
        start = _utc(start).to_pydatetime()
        end = min(_utc(end).to_pydatetime(), now + timedelta(minutes=1))
        chain = [interval]
        if fallback and interval in FALLBACK_CHAIN:
            chain = FALLBACK_CHAIN[FALLBACK_CHAIN.index(interval):]
        for iv in chain:
            s = max(start, now - MAX_LOOKBACK.get(iv, timedelta(days=36500)))
            if s >= end:
                continue
            span = MAX_SPAN.get(iv)
            parts = []
            cur = s
            while cur < end:
                nxt = min(end, cur + span) if span else end
                try:
                    df = self.fetch_bars_once(symbols, iv, start=cur - INTERVAL_DELTA[iv],
                                              end=nxt + timedelta(days=1) if iv == "1d" else nxt)
                except Exception as e:  # network / yahoo errors
                    log.warning("yahoo %s download failed %s..%s: %s", iv, cur, nxt, e)
                    df = empty_bars()
                if len(df):
                    parts.append(df)
                cur = nxt
            if parts:
                out = pd.concat(parts, ignore_index=True)
                out = out[(out["market_ts"] >= pd.Timestamp(start)) & (out["market_ts"] <= pd.Timestamp(end))]
                out = out.drop_duplicates(["symbol", "market_ts", "interval"], keep="last")
                if len(out):
                    if iv != interval:
                        log.warning("yahoo: %s unavailable, fell back to %s", interval, iv)
                    return out.sort_values(["symbol", "market_ts"]).reset_index(drop=True)
        return empty_bars()

    def fetch_recent(self, symbols: list[str], interval: str = "1m", period: str = "1d") -> pd.DataFrame:
        """Today's bars (used by the live poller: one batched request)."""
        return self.fetch_bars_once(symbols, interval, period=period)

    def fetch_quotes(self, symbols: list[str]) -> list[Quote]:
        """Last 1m close per symbol as a Quote (delayed)."""
        df = self.fetch_recent(symbols)
        out = []
        for sym, g in df.groupby("symbol"):
            r = g.iloc[-1]
            out.append(Quote(symbol=sym, instrument_type=InstrumentType(r["instrument_type"]),
                             price=float(r["close"]), market_ts=r["market_ts"].to_pydatetime(),
                             received_ts=r["received_ts"].to_pydatetime(), source=self.name,
                             latency_class=self.latency_class,
                             volume=None if pd.isna(r["volume"]) else float(r["volume"])))
        return out


def measure_delay(bars: pd.DataFrame) -> pd.DataFrame:
    """Per symbol: received_ts - latest market_ts (seconds)."""
    if bars is None or len(bars) == 0:
        return pd.DataFrame(columns=["symbol", "latest_market_ts", "received_ts", "delay_s"])
    g = bars.groupby("symbol").agg(latest_market_ts=("market_ts", "max"), received_ts=("received_ts", "max"))
    g["delay_s"] = (g["received_ts"] - g["latest_market_ts"]).dt.total_seconds()
    return g.reset_index()
