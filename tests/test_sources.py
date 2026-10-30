"""Tests for bist30.sources (offline, recorded fixtures in data/raw/fixtures).

Network tests run only with env BIST30_NETWORK=1.
"""

from __future__ import annotations

import io
import os
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from bist30.models import BAR_COLUMNS, InstrumentType, LatencyClass, OrderBookSnapshot, Quote
from bist30.sources import available_sources, get_source, store
from bist30.sources import borsaistanbul as bi
from bist30.sources import foreks as fx
from bist30.sources import rates
from bist30.sources import yahoo as yh

FIX = Path(__file__).resolve().parents[1] / "data" / "raw" / "fixtures"
network = pytest.mark.skipif(not os.environ.get("BIST30_NETWORK"), reason="set BIST30_NETWORK=1")


def _txt(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8-sig")


# ----------------------------------------------------------------------------- registry
def test_registry():
    assert {"yahoo", "borsaistanbul", "foreks"} <= set(available_sources())
    s = get_source("yahoo")
    assert s.name == "yahoo" and s.latency_class == LatencyClass.DELAYED_15M
    assert get_source("borsaistanbul").latency_class == LatencyClass.EOD
    assert get_source("foreks").latency_class == LatencyClass.DELAYED_15M
    with pytest.raises(KeyError):
        get_source("nope")


# ----------------------------------------------------------------------------- yahoo
def _yahoo_raw(name):
    return pd.read_csv(FIX / name, header=[0, 1], index_col=0, parse_dates=False)


def test_yahoo_normalize_1m():
    raw = _yahoo_raw("yahoo_1m_raw.csv")
    raw.index = pd.to_datetime(raw.index, utc=False)
    rec = datetime(2026, 10, 30, 10, 50, tzinfo=timezone.utc)
    df = yh.normalize_download(raw, ["XU030", "AKBNK", "GARAN"], "1m", rec)
    assert list(df.columns) == BAR_COLUMNS
    assert set(df.symbol) == {"XU030", "AKBNK", "GARAN"}
    assert str(df.market_ts.dt.tz) == "UTC"
    # bar END = start + 1 minute; first raw label 13:17+03:00 -> start 10:17Z, end 10:18Z
    x = df[df.symbol == "XU030"].iloc[0]
    assert x.bar_start == pd.Timestamp("2026-10-30 10:17", tz="UTC")
    assert x.market_ts - x.bar_start == pd.Timedelta(minutes=1)
    assert x.instrument_type == "index" and pd.isna(x.volume)       # index volume 0 -> NaN
    a = df[df.symbol == "AKBNK"]
    assert a.instrument_type.eq("equity").all() and a.volume.notna().all()
    assert (df.latency_class == "delayed_15m").all() and (df.received_ts == pd.Timestamp(rec)).all()
    d = yh.measure_delay(df)
    assert (d.delay_s > 0).all()


def test_yahoo_normalize_1d_close_time():
    raw = _yahoo_raw("yahoo_1d_raw.csv")
    raw.index = pd.to_datetime(raw.index)
    df = yh.normalize_download(raw, ["XU030", "AKBNK"], "1d", datetime.now(timezone.utc))
    ist = df.market_ts.dt.tz_convert("Europe/Istanbul")
    assert (ist.dt.hour == 18).all() and (ist.dt.minute == 10).all()
    # official close for 2026-10-28 was 13960.89 (Borsa Istanbul PayEndeksleri)
    r = df[(df.symbol == "XU030") & (ist.dt.date == date(2026, 10, 28))]
    if len(r):
        assert abs(r.close.iloc[0] - 13960.89) < 0.05


def test_yahoo_symbol_mapping():
    assert yh.to_yahoo("akbnk") == "AKBNK.IS" and yh.to_yahoo("XU030.IS") == "XU030.IS"
    assert yh.from_yahoo("XU030.IS") == "XU030"
    assert yh.instrument_type("XU030") == InstrumentType.INDEX


def test_yahoo_empty():
    assert list(yh.normalize_download(pd.DataFrame(), ["X"], "1m", datetime.now(timezone.utc)).columns) == BAR_COLUMNS


# ----------------------------------------------------------------------------- foreks
def test_foreks_canonical():
    assert fx.canonical_from_name("XU030 (31 Ara 26) Vadeli") == ("F_XU0301226", "2026-12-31")
    assert fx.canonical_from_name("XU030 (26 Şub 27) Vadeli") == ("F_XU0300227", "2027-02-26")
    assert fx.canonical_from_name("XU030 (30 Eki 26) Vadeli")[0] == "F_XU0301026"
    assert fx.canonical_from_name("garbage") is None


def test_foreks_list_page():
    rows = fx.parse_list_page(_txt("foreks_vadeli_islemler.html"))
    syms = [r["symbol"] for r in rows]
    assert "F_XU0301226" in syms
    for r in rows:
        assert r["price"] > 1000 and r["market_ts"].tzinfo is not None and r["url"].startswith("https://")
    assert syms == sorted(syms, key=lambda s: (s[-2:], s[-4:-2]))  # sorted by expiry


def test_foreks_detail_page():
    d = fx.parse_detail_page(_txt("foreks_detail_VXU0302.html"))
    assert d["symbol"] == "F_XU0301226"
    assert d["bid"] <= d["price"] <= d["ask"] or d["bid"] < d["ask"]
    assert d["low"] <= d["price"] <= d["high"]
    assert str(d["market_ts"].tz) == "UTC" and d["volume"] > 0


def test_foreks_bars_from_quotes():
    t0 = pd.Timestamp("2026-10-30 10:00:05", tz="UTC")
    q = pd.DataFrame({"symbol": "F_XU0301226", "market_ts": [t0, t0 + pd.Timedelta(seconds=20),
                                                             t0 + pd.Timedelta(seconds=70)],
                      "price": [100.0, 102.0, 101.0], "volume": [10.0, 15.0, 20.0],
                      "received_ts": [t0 + pd.Timedelta(minutes=15)] * 3})
    b = fx.bars_from_quotes(q)
    assert list(b.columns) == BAR_COLUMNS and len(b) == 2
    assert b.iloc[0].high == 102 and b.iloc[0].close == 102 and b.iloc[0].market_ts == pd.Timestamp("2026-10-30 10:01", tz="UTC")


# ----------------------------------------------------------------------------- borsa istanbul
def test_bist_index_file():
    z = zipfile.ZipFile(FIX / "PayEndeksleri.zip")
    text = z.read([n for n in z.namelist() if "Fiyat" in n][0]).decode("utf-8-sig")
    rows = bi.parse_index_csv(text)
    x = rows[rows.code == "XU030"].iloc[0]
    assert x.date == date(2026, 10, 28) and x.close == pytest.approx(13960.89)
    bars = bi.index_rows_to_bars(rows, ("XU030",))
    assert list(bars.columns) == BAR_COLUMNS and bars.latency_class.iloc[0] == "eod"
    assert bars.market_ts.iloc[0] == pd.Timestamp("2026-10-28 15:10", tz="UTC")


def test_bist_membership():
    m = bi.parse_membership_csv(_txt("hisse_endeks_ds_xu030.csv"))
    x = m[m.index_code == "XU030"]
    assert len(x) == 30 and {"AKBNK", "THYAO", "TRALT"} <= set(x.symbol)


def test_bist_equity_bulletin():
    b = bi.parse_equity_bulletin(_txt("thb202610281_sample.csv"))
    a = b[b.symbol == "AKBNK"].iloc[0]
    assert a.close == pytest.approx(63.0) and a.in_xu030 and a.volume > 0
    assert not b.symbol.str.contains("AOF").any()


def test_bist_viop_bulletin():
    v = bi.parse_viop_bulletin(_txt("viop_gecici_tmp_20261028_xu030.csv"))
    r = v[v.symbol == "F_XU0301226"].iloc[0]
    assert r.settlement == 14649 and r.expiry == date(2026, 12, 31)
    bars = bi.daily_rows_to_bars(v[v.symbol == "F_XU0301226"], InstrumentType.FUTURE, bi.VIOP_CLOSE,
                                 price_col="settlement")
    assert bars.close.iloc[0] == 14649 and bars.instrument_type.iloc[0] == "future"


def test_bist_html_is_rejected():
    with pytest.raises(ValueError):
        bi._read_csv("<!DOCTYPE html><html></html>\nx\ny")


# ----------------------------------------------------------------------------- rates
def test_rates_parsers():
    d = rates.parse_tlref_daily(_txt("tlreforani.csv"))
    assert d.rate_pct.iloc[-1] == pytest.approx(35.7733)
    z = zipfile.ZipFile(FIX / "TLREFORANI_D.zip")
    h = rates.parse_tlref_hist(z.read(z.namelist()[0]))
    assert len(h) > 1000 and h.date.is_monotonic_increasing
    assert h[h.date == date(2026, 10, 28)].rate_pct.iloc[0] == pytest.approx(35.7733)
    t = rates.parse_tcmb_repo(_txt("tcmb_1hafta_repo.html"))
    assert t.iloc[-1].rate_pct == pytest.approx(36.0)


def test_rates_fallback(monkeypatch):
    monkeypatch.setenv("BIST30_FALLBACK_RATE", "0.4")
    r = rates.get_short_rate(use_network=False)
    assert r.source == "fallback" and r.rate == 0.4


# ----------------------------------------------------------------------------- store
def _bars(close=10.0, ts="2026-10-30 10:01"):
    t = pd.Timestamp(ts, tz="UTC")
    return pd.DataFrame([{"symbol": "AKBNK", "instrument_type": "equity", "bar_start": t - pd.Timedelta(minutes=1),
                          "market_ts": t, "open": 9.0, "high": 11.0, "low": 8.0, "close": close, "volume": 5.0,
                          "source": "yahoo", "latency_class": "delayed_15m", "interval": "1m",
                          "received_ts": t + pd.Timedelta(minutes=15)}])


def test_store_roundtrip_and_dedupe(tmp_path):
    db = tmp_path / "m.sqlite"
    assert store.append_bars(_bars(), path=db) == 1
    assert store.append_bars(_bars(), path=db) == 0            # identical -> no change
    b2 = _bars(close=10.5)
    b2["received_ts"] = b2["received_ts"] + pd.Timedelta(minutes=1)
    assert store.append_bars(b2, path=db) == 1                 # partial bar updated
    out = store.read_bars(["AKBNK"], "2026-10-30", "2026-10-31", path=db, include_first_received=True)
    assert len(out) == 1 and out.close.iloc[0] == 10.5
    assert out.first_received_ts.iloc[0] < out.received_ts.iloc[0]
    assert str(out.market_ts.dt.tz) == "UTC"
    assert len(store.read_bars(None, latency_class=LatencyClass.EOD, path=db)) == 0
    assert len(store.read_bars(None, source="yahoo", path=db)) == 1


def test_store_quotes_orderbook(tmp_path):
    db = tmp_path / "m.sqlite"
    now = datetime(2026, 10, 30, 11, 0, tzinfo=timezone.utc)
    q = Quote("F_XU0301226", InstrumentType.FUTURE, 14330.0, now - timedelta(minutes=15), now, "foreks",
              LatencyClass.DELAYED_15M, volume=1.0, bid=14329.0, ask=14331.0)
    assert store.append_quotes([q], extra={q.symbol: {"open": 1}}, path=db) == 1
    assert store.append_quotes([q], path=db) == 0
    r = store.read_quotes(["F_XU0301226"], path=db)
    assert r.bid.iloc[0] == 14329.0 and r.market_ts.iloc[0] == pd.Timestamp(now - timedelta(minutes=15))
    s = OrderBookSnapshot("F_XU0301226", now, now, "x", LatencyClass.DELAYED_15M, bids=[(1.0, 2.0)], asks=[(3.0, 4.0)])
    assert store.append_orderbook([s], path=db) == 1
    assert store.read_orderbook(path=db)[0].asks == [(3.0, 4.0)]


# ----------------------------------------------------------------------------- network
@network
def test_net_yahoo():
    df = get_source("yahoo").fetch_bars(["XU030", "AKBNK"], datetime.now(timezone.utc) - timedelta(days=3),
                                        datetime.now(timezone.utc))
    assert len(df) > 0 and set(df.symbol) <= {"XU030", "AKBNK"}


@network
def test_net_foreks():
    qs = get_source("foreks").fetch_quotes()
    assert qs and all(q.symbol.startswith("F_XU030") for q in qs)


@network
def test_net_bist_and_rate():
    rows = get_source("borsaistanbul").fetch_index_eod()
    assert "XU030" in set(rows.code)
    assert 0 < rates.get_short_rate().rate < 1
