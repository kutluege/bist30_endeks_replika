"""SQLite storage for bars, quotes, order-book snapshots and delay measurements.

DB: ``data/db/market.sqlite`` (override with env ``BIST30_DB`` or the ``db_path`` argument).

All timestamps are stored as UTC ISO-8601 strings with a fixed format
(``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``) so lexical order == time order.

Bars are de-duplicated on (symbol, market_ts, source, interval). Re-inserting a bar
whose OHLCV changed (e.g. the last, still-forming Yahoo bar) updates the values and
``received_ts``; ``first_received_ts`` always keeps the first time we saw the bar.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

import pandas as pd

from bist30.models import BAR_COLUMNS, LatencyClass, OrderBookSnapshot, Quote

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = PROJECT_ROOT / "data" / "db" / "market.sqlite"

_TS_FMT = "%Y-%m-%dT%H:%M:%S.%f+00:00"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    instrument_type TEXT,
    bar_start TEXT,
    market_ts TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL,
    source TEXT NOT NULL,
    latency_class TEXT,
    interval TEXT NOT NULL,
    received_ts TEXT,
    first_received_ts TEXT,
    PRIMARY KEY (symbol, market_ts, source, interval)
);
CREATE INDEX IF NOT EXISTS ix_bars_ts ON bars(market_ts);
CREATE TABLE IF NOT EXISTS quotes (
    symbol TEXT NOT NULL,
    instrument_type TEXT,
    price REAL,
    market_ts TEXT NOT NULL,
    received_ts TEXT NOT NULL,
    source TEXT NOT NULL,
    latency_class TEXT,
    volume REAL, bid REAL, ask REAL, bid_size REAL, ask_size REAL,
    extra TEXT,
    PRIMARY KEY (symbol, source, market_ts, received_ts)
);
CREATE INDEX IF NOT EXISTS ix_quotes_ts ON quotes(symbol, market_ts);
CREATE TABLE IF NOT EXISTS orderbook (
    symbol TEXT NOT NULL,
    market_ts TEXT NOT NULL,
    received_ts TEXT NOT NULL,
    source TEXT NOT NULL,
    latency_class TEXT,
    bids TEXT, asks TEXT,
    PRIMARY KEY (symbol, source, market_ts, received_ts)
);
CREATE TABLE IF NOT EXISTS delay_log (
    received_ts TEXT NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    latest_market_ts TEXT,
    delay_s REAL,
    PRIMARY KEY (received_ts, source, symbol)
);
"""


def db_path(path: Optional[os.PathLike | str] = None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get("BIST30_DB")
    return Path(env) if env else DEFAULT_DB


@contextmanager
def connect(path: Optional[os.PathLike | str] = None):
    p = db_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(_SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------------------- helpers
def to_utc_ts(x) -> pd.Timestamp:
    ts = pd.Timestamp(x)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def ts_to_str(x) -> Optional[str]:
    if x is None or (not isinstance(x, (str, datetime, pd.Timestamp)) and pd.isna(x)):
        return None
    if isinstance(x, pd.Timestamp) and pd.isna(x):
        return None
    return to_utc_ts(x).strftime(_TS_FMT)


def _ser_to_str(s: pd.Series) -> pd.Series:
    s = pd.to_datetime(s, utc=True)
    return s.dt.strftime(_TS_FMT)


def _f(x):
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(v) else v


def _enum_val(x):
    return x.value if hasattr(x, "value") else x


# --------------------------------------------------------------------------- bars
def append_bars(df: pd.DataFrame, path=None) -> int:
    """Upsert a BAR_COLUMNS frame. Returns number of rows inserted or changed."""
    if df is None or len(df) == 0:
        return 0
    missing = [c for c in BAR_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"bars frame missing columns: {missing}")
    d = df[BAR_COLUMNS].copy()
    for c in ("bar_start", "market_ts", "received_ts"):
        d[c] = _ser_to_str(d[c])
    d["instrument_type"] = d["instrument_type"].map(_enum_val)
    d["latency_class"] = d["latency_class"].map(_enum_val)
    rows = []
    for r in d.itertuples(index=False):
        rows.append((r.symbol, r.instrument_type, r.bar_start, r.market_ts,
                     _f(r.open), _f(r.high), _f(r.low), _f(r.close), _f(r.volume),
                     r.source, r.latency_class, r.interval, r.received_ts, r.received_ts))
    sql = """
    INSERT INTO bars (symbol, instrument_type, bar_start, market_ts, open, high, low, close,
                      volume, source, latency_class, interval, received_ts, first_received_ts)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(symbol, market_ts, source, interval) DO UPDATE SET
        open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
        volume=excluded.volume, received_ts=excluded.received_ts,
        latency_class=excluded.latency_class
    WHERE bars.open IS NOT excluded.open OR bars.high IS NOT excluded.high
       OR bars.low IS NOT excluded.low OR bars.close IS NOT excluded.close
       OR bars.volume IS NOT excluded.volume
    """
    with connect(path) as con:
        before = con.total_changes
        con.executemany(sql, rows)
        return con.total_changes - before


def read_bars(symbols: Optional[Sequence[str]] = None, start=None, end=None,
              source: Optional[str] = None, latency_class=None, interval: Optional[str] = None,
              path=None, include_first_received: bool = False) -> pd.DataFrame:
    """Read bars with market_ts in [start, end]. Returns BAR_COLUMNS (UTC tz-aware)."""
    q = "SELECT * FROM bars WHERE 1=1"
    params: list = []
    if symbols:
        symbols = list(symbols)
        q += f" AND symbol IN ({','.join('?' * len(symbols))})"
        params += symbols
    if start is not None:
        q += " AND market_ts >= ?"
        params.append(ts_to_str(start))
    if end is not None:
        q += " AND market_ts <= ?"
        params.append(ts_to_str(end))
    if source:
        q += " AND source = ?"
        params.append(source)
    if latency_class is not None:
        q += " AND latency_class = ?"
        params.append(_enum_val(latency_class))
    if interval:
        q += " AND interval = ?"
        params.append(interval)
    q += " ORDER BY symbol, market_ts"
    with connect(path) as con:
        df = pd.read_sql_query(q, con, params=params)
    for c in ("bar_start", "market_ts", "received_ts", "first_received_ts"):
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
    cols = BAR_COLUMNS + (["first_received_ts"] if include_first_received else [])
    return df[cols].reset_index(drop=True)


# --------------------------------------------------------------------------- quotes
def append_quotes(quotes: Iterable[Quote], extra: Optional[dict] = None, path=None) -> int:
    rows = []
    for q in quotes:
        ex = getattr(q, "_extra", None)
        rows.append((q.symbol, _enum_val(q.instrument_type), _f(q.price), ts_to_str(q.market_ts),
                     ts_to_str(q.received_ts), q.source, _enum_val(q.latency_class), _f(q.volume),
                     _f(q.bid), _f(q.ask), _f(q.bid_size), _f(q.ask_size),
                     json.dumps((extra or {}).get(q.symbol, ex) or {}, ensure_ascii=False)))
    if not rows:
        return 0
    with connect(path) as con:
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        return con.total_changes - before


def read_quotes(symbols: Optional[Sequence[str]] = None, start=None, end=None,
                source: Optional[str] = None, latency_class=None, path=None,
                dedupe_market_ts: bool = False) -> pd.DataFrame:
    """Read quotes with market_ts in [start, end].

    ``dedupe_market_ts=True`` keeps only the first observation per (symbol, source, market_ts)
    (the poller may observe the same delayed quote several times).
    """
    q = "SELECT * FROM quotes WHERE 1=1"
    params: list = []
    if symbols:
        symbols = list(symbols)
        q += f" AND symbol IN ({','.join('?' * len(symbols))})"
        params += symbols
    if start is not None:
        q += " AND market_ts >= ?"
        params.append(ts_to_str(start))
    if end is not None:
        q += " AND market_ts <= ?"
        params.append(ts_to_str(end))
    if source:
        q += " AND source = ?"
        params.append(source)
    if latency_class is not None:
        q += " AND latency_class = ?"
        params.append(_enum_val(latency_class))
    q += " ORDER BY symbol, market_ts, received_ts"
    with connect(path) as con:
        df = pd.read_sql_query(q, con, params=params)
    for c in ("market_ts", "received_ts"):
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
    if dedupe_market_ts and len(df):
        df = df.drop_duplicates(["symbol", "source", "market_ts"], keep="first")
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- order book
def append_orderbook(snaps: Iterable[OrderBookSnapshot], path=None) -> int:
    rows = [(s.symbol, ts_to_str(s.market_ts), ts_to_str(s.received_ts), s.source,
             _enum_val(s.latency_class), json.dumps(s.bids), json.dumps(s.asks)) for s in snaps]
    if not rows:
        return 0
    with connect(path) as con:
        before = con.total_changes
        con.executemany("INSERT OR IGNORE INTO orderbook VALUES (?,?,?,?,?,?,?)", rows)
        return con.total_changes - before


def read_orderbook(symbols=None, start=None, end=None, source=None, path=None) -> list[OrderBookSnapshot]:
    q = "SELECT * FROM orderbook WHERE 1=1"
    params: list = []
    if symbols:
        symbols = list(symbols)
        q += f" AND symbol IN ({','.join('?' * len(symbols))})"
        params += symbols
    if start is not None:
        q += " AND market_ts >= ?"
        params.append(ts_to_str(start))
    if end is not None:
        q += " AND market_ts <= ?"
        params.append(ts_to_str(end))
    if source:
        q += " AND source = ?"
        params.append(source)
    with connect(path) as con:
        rows = con.execute(q + " ORDER BY market_ts", params).fetchall()
    return [OrderBookSnapshot(symbol=r[0], market_ts=to_utc_ts(r[1]).to_pydatetime(),
                              received_ts=to_utc_ts(r[2]).to_pydatetime(), source=r[3],
                              latency_class=LatencyClass(r[4]),
                              bids=[tuple(x) for x in json.loads(r[5])],
                              asks=[tuple(x) for x in json.loads(r[6])]) for r in rows]


# --------------------------------------------------------------------------- delay log
def append_delays(rows: Iterable[tuple], path=None) -> int:
    """rows: (received_ts, source, symbol, latest_market_ts, delay_s)."""
    rows = [(ts_to_str(a), b, c, ts_to_str(d), _f(e)) for a, b, c, d, e in rows]
    if not rows:
        return 0
    with connect(path) as con:
        con.executemany("INSERT OR IGNORE INTO delay_log VALUES (?,?,?,?,?)", rows)
        return len(rows)


def read_delays(source=None, path=None) -> pd.DataFrame:
    q, params = "SELECT * FROM delay_log", []
    if source:
        q += " WHERE source = ?"
        params.append(source)
    with connect(path) as con:
        df = pd.read_sql_query(q + " ORDER BY received_ts", con, params=params)
    for c in ("received_ts", "latest_market_ts"):
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
    return df


def counts(path=None) -> dict:
    with connect(path) as con:
        out = {}
        for t in ("bars", "quotes", "orderbook", "delay_log"):
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        out["bars_by_source_interval"] = con.execute(
            "SELECT source, interval, latency_class, COUNT(*), COUNT(DISTINCT symbol), MIN(market_ts), MAX(market_ts) "
            "FROM bars GROUP BY 1,2,3").fetchall()
        out["quotes_by_symbol"] = con.execute(
            "SELECT symbol, source, COUNT(*), COUNT(DISTINCT market_ts), MAX(market_ts) FROM quotes GROUP BY 1,2").fetchall()
    return out
