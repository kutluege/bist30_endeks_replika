"""Official Borsa İstanbul end-of-day files (www.borsaistanbul.com, robots.txt: Allow /).

File catalogue: https://www.borsaistanbul.com/files/DataFilePaths.zip and
https://www.borsaistanbul.com/files/datafilepaths_viop.zip (copies in data/raw/borsaistanbul/).

* ``/datum/PayEndeksleri.zip``   -> latest day's official close/open/low/high of all BIST equity
  price & return indices (XU030 included). Only the LATEST day is published -> collect daily.
* ``/datum/hisse_endeks_ds.csv`` -> current index membership (constituent list per index).
* ``/data/thb/YYYY/MM/thbYYYYMMDD1.zip`` -> equity market daily bulletin (official close,
  closing-auction price, VWAP, remaining best bid/ask, volume) for every instrument; archive.
* ``/viopdata/viop_gecici_tmp_YYYYMMDD.csv`` -> VİOP daily (temporary) bulletin: settlement,
  OHLC, close, VWAP, volume per contract; archive. (``viop_YYYYMMDD.csv`` final bulletin was
  not downloadable on 2026-10-30 - server returned an HTML page.)

All are ``LatencyClass.EOD``. ``market_ts`` for daily records = 18:10 Istanbul (equity closing
auction) for equities/indices and 18:15 Istanbul for VİOP (end of the evening session is not
covered - VİOP settlement is computed from the 17:40-18:10 window; we use the session end).
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from bist30.models import BAR_COLUMNS, InstrumentType, LatencyClass, empty_bars

log = logging.getLogger(__name__)

BASE = "https://www.borsaistanbul.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) bist30-research"
EQ_CLOSE = (18, 10)
VIOP_CLOSE = (18, 15)


def _close_ts(d, hm) -> pd.Timestamp:
    return (pd.Timestamp(d).tz_localize("Europe/Istanbul").normalize()
            + pd.Timedelta(hours=hm[0], minutes=hm[1])).tz_convert("UTC")


def _day_start(d) -> pd.Timestamp:
    return pd.Timestamp(d).tz_localize("Europe/Istanbul").normalize().tz_convert("UTC")


def _local_date(x) -> date:
    t = pd.Timestamp(x)
    if t.tzinfo is not None:
        t = t.tz_convert("Europe/Istanbul")
    return t.date()


def _read_csv(text: str, header_rows: int = 2) -> pd.DataFrame:
    """BIST files: 1st row Turkish header, 2nd row English header, ';' separated."""
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("<"):
        raise ValueError("got HTML instead of CSV (file not published)")
    df = pd.read_csv(io.StringIO("\n".join([lines[1]] + lines[header_rows:])), sep=";", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return df


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.strip().replace({"": None}), errors="coerce")


# ------------------------------------------------------------------------------ parsers
def parse_index_csv(text: str) -> pd.DataFrame:
    """FiyatEndeksleri_PriceIndices.csv -> columns code, date, close, open, low, high."""
    df = _read_csv(text)
    out = pd.DataFrame({
        "code": df["INDEX CODE"].str.strip(),
        "date": pd.to_datetime(df["DATE"], format="%d/%m/%Y").dt.date,
        "close": _num(df["CLOSING VALUE"]), "open": _num(df["OPEN VALUE"]),
        "low": _num(df["LOWEST VALUE"]), "high": _num(df["HIGHEST VALUE"]),
        "currency": df["CURRENCY TYPE"].str.strip(),
    })
    return out


def index_rows_to_bars(rows: pd.DataFrame, codes=("XU030",), received_ts=None,
                       source="borsaistanbul") -> pd.DataFrame:
    rows = rows[rows["code"].isin(codes)]
    if rows.empty:
        return empty_bars()
    rec = pd.Timestamp(received_ts or datetime.now(timezone.utc))
    out = pd.DataFrame({
        "symbol": rows["code"].values, "instrument_type": InstrumentType.INDEX.value,
        "bar_start": [_day_start(d) for d in rows["date"]],
        "market_ts": [_close_ts(d, EQ_CLOSE) for d in rows["date"]],
        "open": rows["open"].values, "high": rows["high"].values, "low": rows["low"].values,
        "close": rows["close"].values, "volume": float("nan"), "source": source,
        "latency_class": LatencyClass.EOD.value, "interval": "1d", "received_ts": rec})
    return out[BAR_COLUMNS]


def parse_membership_csv(text: str) -> pd.DataFrame:
    """hisse_endeks_ds.csv -> columns symbol, name, index_code, date."""
    df = _read_csv(text)
    cols = list(df.columns)
    return pd.DataFrame({
        "symbol": df[cols[0]].str.strip().str.replace(r"\.E$", "", regex=True),
        "name": df[cols[1]].str.strip(), "index_code": df[cols[2]].str.strip(),
        "date": pd.to_datetime(df[cols[5]], format="%d/%m/%Y").dt.date})


def parse_equity_bulletin(text: str) -> pd.DataFrame:
    """thbYYYYMMDD1.csv -> one row per equity (.E) with official EOD fields."""
    df = _read_csv(text)
    df = df[df["INSTRUMENT SERIES CODE"].str.strip().str.endswith(".E")]
    out = pd.DataFrame({
        "symbol": df["INSTRUMENT SERIES CODE"].str.strip().str.replace(r"\.E$", "", regex=True),
        "date": pd.to_datetime(df["TRADE DATE"]).dt.date,
        "prev_close": _num(df["PREVIOUS LAST PRICE"]), "open": _num(df["OPENING PRICE"]),
        "low": _num(df["LOWEST PRICE"]), "high": _num(df["HIGHEST PRICE"]),
        "close": _num(df["CLOSING PRICE"]), "closing_session_price": _num(df["CLOSING SESSION PRICE"]),
        "vwap": _num(df["VWAP"]), "volume": _num(df["TOTAL TRADED VOLUME"]),
        "traded_value": _num(df["TOTAL TRADED VALUE"]),
        "remaining_bid": _num(df["REMAINING BID"]), "remaining_ask": _num(df["REMAINING ASK"]),
        "in_xu030": df["BIST 30 INDEX"].str.strip() == "1",
        "in_xu100": df["BIST 100 INDEX"].str.strip() == "1"})
    return out.reset_index(drop=True)


def parse_viop_bulletin(text: str) -> pd.DataFrame:
    """viop_gecici_tmp_YYYYMMDD.csv / viop_YYYYMMDD.csv -> futures rows."""
    df = _read_csv(text)
    out = pd.DataFrame({
        "symbol": df["INSTRUMENT SERIES"].str.strip(),
        "date": pd.to_datetime(df["TRADE DATE"]).dt.date,
        "instrument_type": df["INSTRUMENT TYPE"].str.strip(),
        "underlying": df["UNDERLYING"].str.strip(),
        "expiry": pd.to_datetime(df["EXPIRATION DATE"], errors="coerce").dt.date,
        "settlement": _num(df["SETTLEMENT PRICE"]), "prev_settlement": _num(df["PREVIOUS SETTLEMENT PRICE"]),
        "open": _num(df["OPENING PRICE"]), "low": _num(df["LOWEST PRICE"]), "high": _num(df["HIGHEST PRICE"]),
        "close": _num(df["CLOSING PRICE"]), "vwap": _num(df["VWAP"]),
        "traded_value": _num(df["TRADED VALUE"]), "volume": _num(df["TRADE VOLUME"]),
        "open_interest": _num(df["OPEN POSITION"]) if "OPEN POSITION" in df.columns else float("nan"),
    })
    return out


def daily_rows_to_bars(rows: pd.DataFrame, itype: InstrumentType, hm, received_ts=None,
                       source="borsaistanbul", price_col="close") -> pd.DataFrame:
    if rows.empty:
        return empty_bars()
    rec = pd.Timestamp(received_ts or datetime.now(timezone.utc))
    out = pd.DataFrame({
        "symbol": rows["symbol"].values, "instrument_type": itype.value,
        "bar_start": [_day_start(d) for d in rows["date"]],
        "market_ts": [_close_ts(d, hm) for d in rows["date"]],
        "open": rows["open"].values, "high": rows["high"].values, "low": rows["low"].values,
        "close": rows[price_col].values, "volume": rows["volume"].values, "source": source,
        "latency_class": LatencyClass.EOD.value, "interval": "1d", "received_ts": rec})
    out = out[out["close"].notna() & (out["close"] > 0)]
    return out[BAR_COLUMNS].reset_index(drop=True)


# ------------------------------------------------------------------------------ source
class BorsaIstanbulSource:
    name = "borsaistanbul"
    latency_class = LatencyClass.EOD

    def __init__(self, raw_dir: Optional[Path] = None, timeout: int = 60):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})
        self.timeout = timeout
        self.raw_dir = raw_dir  # if set, downloaded files are archived here

    def _get(self, path: str) -> bytes:
        r = self.s.get(BASE + path, timeout=self.timeout)
        if r.status_code != 200 or r.content[:20].lstrip().startswith(b"<"):
            raise FileNotFoundError(f"{BASE + path}: HTTP {r.status_code} / not a data file")
        if self.raw_dir:
            p = Path(self.raw_dir) / path.lstrip("/")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(r.content)
        return r.content

    @staticmethod
    def _unzip_text(b: bytes, contains: str = "") -> str:
        z = zipfile.ZipFile(io.BytesIO(b))
        names = [n for n in z.namelist() if not n.startswith("__MACOSX") and contains in n]
        return z.read(names[0]).decode("utf-8-sig", errors="replace")

    # official index values (latest day only)
    def fetch_index_eod(self) -> pd.DataFrame:
        b = self._get("/datum/PayEndeksleri.zip")
        return parse_index_csv(self._unzip_text(b, "Fiyat"))

    def fetch_membership(self) -> pd.DataFrame:
        return parse_membership_csv(self._get("/datum/hisse_endeks_ds.csv").decode("utf-8-sig", "replace"))

    def fetch_equity_bulletin(self, d: date) -> pd.DataFrame:
        b = self._get(f"/data/thb/{d:%Y}/{d:%m}/thb{d:%Y%m%d}1.zip")
        return parse_equity_bulletin(self._unzip_text(b))

    def fetch_viop_bulletin(self, d: date) -> pd.DataFrame:
        last_err = None
        for name in (f"viop_{d:%Y%m%d}.csv", f"viop_gecici_tmp_{d:%Y%m%d}.csv"):
            try:
                return parse_viop_bulletin(self._get("/viopdata/" + name).decode("utf-8-sig", "replace"))
            except (FileNotFoundError, ValueError, KeyError) as e:
                last_err = e
        raise FileNotFoundError(str(last_err))

    def fetch_bars(self, symbols: list[str], start, end, interval: str = "1d") -> pd.DataFrame:
        """Official daily bars. Index symbols (XU030, ...) only for the latest published day;
        equities from the daily bulletin archive; futures (F_...) settlement as close."""
        if interval != "1d":
            raise ValueError("borsaistanbul: only 1d (EOD) data")
        syms = set(symbols)
        idx = {s for s in syms if s.startswith("X")}
        fut = {s for s in syms if s.startswith("F_")}
        eq = syms - idx - fut
        frames = []
        rec = datetime.now(timezone.utc)
        if idx:
            try:
                rows = self.fetch_index_eod()
                frames.append(index_rows_to_bars(rows, tuple(idx), rec, self.name))
            except Exception as e:
                log.warning("borsaistanbul index eod failed: %s", e)
        days = pd.bdate_range(_local_date(start), _local_date(end))
        for d in days.date:
            if eq:
                try:
                    b = self.fetch_equity_bulletin(d)
                    frames.append(daily_rows_to_bars(b[b.symbol.isin(eq)], InstrumentType.EQUITY, EQ_CLOSE, rec, self.name))
                except Exception as e:
                    log.info("no equity bulletin %s: %s", d, e)
            if fut:
                try:
                    v = self.fetch_viop_bulletin(d)
                    frames.append(daily_rows_to_bars(v[v.symbol.isin(fut)], InstrumentType.FUTURE, VIOP_CLOSE,
                                                     rec, self.name, price_col="settlement"))
                except Exception as e:
                    log.info("no viop bulletin %s: %s", d, e)
        frames = [f for f in frames if len(f)]
        if not frames:
            return empty_bars()
        out = pd.concat(frames, ignore_index=True)
        return out.sort_values(["symbol", "market_ts"]).reset_index(drop=True)

    def fetch_quotes(self, symbols):
        raise NotImplementedError("borsaistanbul: EOD files only")
