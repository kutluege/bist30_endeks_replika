"""End-to-end retrospective (out-of-sample) replication run used by the HTML report.

Design (orchestrator decision): calibrate the divisor ONCE on the official close of
the calibration day (default 2026-10-01, first day of the Oct–Dec index period)
from daily closing bars, then evaluate every 1-minute point of every later day
with ``compute_index_series``. Rule-5.1 events (e.g. PETKM FDPO 28.10.2026)
come from ``corporate_actions.csv`` via ``actions=``.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from bist30.engine import active_constituents, calibrate_divisor, compute_index_series, raw_mcap
from bist30.engine.divisor import round_index
from bist30.models import ISTANBUL_TZ, Constituent, DivisorState

log = logging.getLogger(__name__)
INDEX_SYMBOL = "XU030"


@dataclass
class BacktestRun:
    results: pd.DataFrame
    constituents: list[Constituent]          # all rows used (may include period changes)
    calib_constituents: list[Constituent]    # the 30 active at calibration
    divisor_state: DivisorState              # calibration state (before any 5.1 adjustment)
    final_divisor_state: DivisorState
    calibration_date: date
    events: list[dict] = field(default_factory=list)
    close_check: list[dict] = field(default_factory=list)
    info: dict = field(default_factory=dict)


# ------------------------------------------------------------------ inputs
def trading_days(bars: pd.DataFrame) -> list[date]:
    lt = pd.to_datetime(bars["market_ts"], utc=True).dt.tz_convert(ISTANBUL_TZ)
    return sorted(set(lt.dt.date))


def load_constituents_union(days: list[date]) -> tuple[list[Constituent], str]:
    """Union of the constituent rows active on each day (keeps period changes such as
    PETKM's FDPO row from 28.10). Returns (constituents, origin)."""
    from bist30.metadata import load_constituents
    rows: dict[tuple, Constituent] = {}
    for d in days:
        for c in load_constituents(as_of=d):
            rows[(c.symbol, c.effective_from)] = c
    return list(rows.values()), "constituents.csv"


def provisional_constituents_from_yahoo(symbols: list[str]) -> list[Constituent]:
    """Fallback only (no constituents.csv): Yahoo sharesOutstanding / floatShares.
    Output must be labelled GEÇİCİ in the report."""
    import yfinance as yf

    def ff_round(h: float) -> float:   # rule 2.19
        pct = h * 100
        return (round(pct) if pct >= 1 else round(pct, 2)) / 100

    out = []
    for s in symbols:
        i = yf.Ticker(f"{s}.IS").info
        out.append(Constituent(s, i.get("longName", s), f"{s}.IS", float(i["sharesOutstanding"]),
                               ff_round(i["floatShares"] / i["sharesOutstanding"]), 1.0))
    return out


def load_bars(symbols: list[str], start=None, end=None, refresh_days: int = 0) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """1m and 1d Yahoo bars (delayed_15m) from the local store; optionally top up the
    last ``refresh_days`` from Yahoo in memory (the store is not written)."""
    from bist30.sources import store
    syms = list(symbols) + [INDEX_SYMBOL]
    m1 = store.read_bars(syms, start=start, end=end, source="yahoo", interval="1m")
    d1 = store.read_bars(syms, start=(pd.Timestamp(start) - pd.Timedelta(days=10)) if start is not None else None,
                         end=end, source="yahoo", interval="1d")
    info = {"store_1m_rows": int(len(m1)), "store_1d_rows": int(len(d1)), "refreshed_1m_rows": 0}
    if refresh_days:
        try:
            from bist30.sources import get_source
            ya = get_source("yahoo")
            now = datetime.now(timezone.utc)
            new = ya.fetch_bars(syms, now - timedelta(days=refresh_days), now, interval="1m", fallback=False)
            newd = ya.fetch_bars(syms, now - timedelta(days=refresh_days + 5), now, interval="1d", fallback=False)
            info["refreshed_1m_rows"] = int(len(new))
            m1 = pd.concat([m1, new], ignore_index=True)
            d1 = pd.concat([d1, newd], ignore_index=True)
        except Exception as e:  # network problems must not kill the report build
            log.warning("yahoo refresh failed: %s", e)
            info["refresh_error"] = str(e)
    for df in (m1, d1):
        df.sort_values(["symbol", "market_ts", "received_ts"], inplace=True)
    m1 = m1.drop_duplicates(["symbol", "market_ts"], keep="last").reset_index(drop=True)
    d1 = d1.drop_duplicates(["symbol", "market_ts"], keep="last").reset_index(drop=True)
    return m1, d1, info


def _daily_for_calibration(d1: pd.DataFrame) -> pd.DataFrame:
    """Yahoo daily bars sometimes report volume 0 although the close is the official
    closing price (e.g. SASA 01.10.2026). For a DAILY close that only means 'volume
    unknown', so 0 -> NaN (the engine then accepts the price)."""
    d = d1.copy()
    d["volume"] = pd.to_numeric(d["volume"], errors="coerce")
    d.loc[d["volume"].eq(0), "volume"] = np.nan
    return d


def _daily_ts(d1: pd.DataFrame, symbol: str, day: date) -> pd.Timestamp:
    s = d1[d1["symbol"] == symbol]
    lt = pd.to_datetime(s["market_ts"], utc=True).dt.tz_convert(ISTANBUL_TZ)
    hit = s[(lt.dt.date == day).to_numpy()]
    if hit.empty:
        raise ValueError(f"no daily {symbol} bar on {day}")
    return pd.Timestamp(hit["market_ts"].iloc[0])


# ------------------------------------------------------------------ run
def run_backtest(m1: pd.DataFrame, d1: pd.DataFrame, constituents: list[Constituent],
                 calibration_date: date, actions: pd.DataFrame | None = None,
                 staleness_s: float = 300.0) -> BacktestRun:
    d1c = _daily_for_calibration(d1)
    calib_cons = active_constituents(constituents, calibration_date)
    syms = sorted({c.symbol for c in constituents})
    ref = _daily_ts(d1c, INDEX_SYMBOL, calibration_date)
    official_d = d1c[d1c["symbol"] == INDEX_SYMBOL]
    state = calibrate_divisor(d1c[d1c["symbol"].isin(syms)], constituents, official_d, ref)

    acts = None
    if actions is not None and len(actions):
        eff = pd.to_datetime(actions["effective_date"]).dt.date
        acts = actions[(eff > calibration_date).to_numpy()].copy()   # <= calibration day is already in N/H
    bars = m1[m1["symbol"].isin(syms + [INDEX_SYMBOL])]
    first_day = pd.Timestamp(calibration_date).tz_localize(ISTANBUL_TZ).tz_convert("UTC")
    bars = bars[pd.to_datetime(bars["market_ts"], utc=True) >= first_day]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        # official closes as the day base (Yahoo 1m lacks the closing-auction print)
        res = compute_index_series(bars, constituents, state, staleness_s=staleness_s, actions=acts,
                                   daily_closes=d1c)
    events = list(res.attrs.get("events", []))
    final_state = res.attrs.get("divisor_state", state)
    run = BacktestRun(results=res, constituents=constituents, calib_constituents=calib_cons,
                      divisor_state=state, final_divisor_state=final_state,
                      calibration_date=calibration_date, events=events)
    run.close_check = daily_close_check(d1c, constituents, res, calibration_date, state)
    run.info = {"n_bars_1m": int(len(bars)), "symbols": syms}
    return run


def daily_close_check(d1: pd.DataFrame, constituents: list[Constituent], res: pd.DataFrame,
                      calibration_date: date, state: DivisorState) -> list[dict]:
    """Our close from DAILY closing prices (these include the closing auction, which
    the 1m data lacks) with that day's divisor, vs the official XU030 close."""
    lt = pd.to_datetime(res["market_ts"], utc=True).dt.tz_convert(ISTANBUL_TZ)
    div_by_day = res.groupby(lt.dt.date.to_numpy())["divisor"].last().to_dict()
    last_1m = {}
    for d, g in res.groupby(lt.dt.date.to_numpy()):
        g = g[g["session_phase"] == "continuous"] if "session_phase" in g.columns else g
        if len(g):
            r = g.iloc[-1]
            last_1m[d] = r
    dl = pd.to_datetime(d1["market_ts"], utc=True).dt.tz_convert(ISTANBUL_TZ).dt.date
    out = []
    for d in sorted(set(div_by_day) | {calibration_date}):
        day_bars = d1[(dl == d).to_numpy()]
        off = day_bars.loc[day_bars["symbol"] == INDEX_SYMBOL, "close"]
        if off.empty:
            continue
        cons = active_constituents(constituents, d)
        prices = dict(zip(day_bars["symbol"], day_bars["close"].astype(float)))
        missing = [c.symbol for c in cons if c.symbol not in prices]
        divisor = state.divisor if d == calibration_date else div_by_day.get(d)
        row = {"date": d.isoformat(), "official_close": float(off.iloc[0]), "divisor": divisor,
               "calibration_day": d == calibration_date, "missing": missing}
        if not missing and divisor:
            calc = round_index(raw_mcap(cons, prices) / divisor)
            row.update(calc_close=calc, diff_pts=calc - row["official_close"],
                       diff_pct=(calc - row["official_close"]) / row["official_close"] * 100)
        r = last_1m.get(d)
        if r is not None:
            row.update(last_1m_ts=pd.Timestamp(r["market_ts"]).tz_convert(ISTANBUL_TZ).isoformat(),
                       last_1m_diff_pct=float(r["diff_pct"]) if pd.notna(r["diff_pct"]) else None,
                       last_1m_official=float(r["official_value"]) if pd.notna(r["official_value"]) else None)
        out.append(row)
    return out
