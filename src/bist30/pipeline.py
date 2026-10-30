"""End-to-end orchestration (owner: orchestrator).

Backtest design (retrospective, out-of-sample):
  1. Calibrate the divisor ONCE on the official close of ``calib_date``
     (Yahoo 1d closes == BIST official closes; same latency class for stocks and XU030).
  2. EOD test: every later day's index from closing prices x N x H / B, with rule-5.1
     divisor adjustments on effective dates, vs the official XU030 close.
  3. Intraday test: every 1-minute grid point after ``calib_date`` from Yahoo 1m bars
     (15-min delayed), vs Yahoo's XU030.IS at the SAME market_ts.
  4. VIOP: Foreks 15-min-delayed futures snapshots vs the same-latency spot.
No step refits the divisor to the official series.
"""

from __future__ import annotations

import copy
import json
import logging
import warnings
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

from bist30.models import ISTANBUL_TZ, Constituent, DivisorState

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*inplace.*")

log = logging.getLogger("bist30.pipeline")

ROOT = Path(__file__).resolve().parents[2]
INDEX = "XU030"


def load_settings(path: Optional[Path] = None) -> dict:
    with open(path or ROOT / "config" / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _ist_day_bounds(d: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = pd.Timestamp(datetime.combine(d, time(0, 0)), tz=ISTANBUL_TZ).tz_convert("UTC")
    return start, start + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)


def _ist_date(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts, utc=True).dt.tz_convert(ISTANBUL_TZ).dt.date


def all_constituents(path: Optional[Path] = None) -> list[Constituent]:
    """Every row of constituents.csv (all validity windows), validated by the loader."""
    from bist30.metadata.loader import DEFAULT_CONSTITUENTS_PATH, _read_constituents_frame, _validate_rows
    df = _read_constituents_frame(path or DEFAULT_CONSTITUENTS_PATH)
    _validate_rows(df)
    return [Constituent(symbol=r.symbol, name=r.name, yahoo_ticker=r.yahoo_ticker,
                        shares_outstanding=float(r.shares_outstanding),
                        free_float_ratio=float(r.free_float_ratio),
                        weight_factor=float(r.weight_factor),
                        effective_from=r.effective_from, effective_to=r.effective_to)
            for r in df.itertuples(index=False)]


def _price_actions(actions: pd.DataFrame, after: date) -> pd.DataFrame:
    a = actions.copy()
    a["effective_date"] = pd.to_datetime(a["effective_date"]).dt.date
    flag = a["affects_price_index"].astype(str).str.lower().isin(["true", "1", "yes"])
    return a[flag & (a["effective_date"] > after)].reset_index(drop=True)


def _daily_closes(d1: pd.DataFrame) -> pd.DataFrame:
    """Yahoo 1d bars: the close is the official closing price, but Yahoo reports volume 0
    for some very high-volume names (e.g. SASA, ~52bn shares). Volume is irrelevant for a
    closing price, so 0 is treated as unknown (NaN) instead of "no trade"."""
    d1 = d1.copy()
    d1.loc[d1["volume"] == 0, "volume"] = np.nan
    return d1


def _defn(cons: list[Constituent]) -> set:
    return {(c.symbol, c.shares_outstanding, c.free_float_ratio, c.weight_factor) for c in cons}


# --------------------------------------------------------------------------- calibration
def calibrate(calib_date: date, cons_all: list[Constituent]) -> DivisorState:
    from bist30.engine import active_constituents, calibrate_divisor
    from bist30.metadata import constituents_version
    from bist30.sources import store

    syms = sorted({c.symbol for c in active_constituents(cons_all, calib_date)})
    s, e = _ist_day_bounds(calib_date)
    d1 = _daily_closes(store.read_bars(syms + [INDEX], start=s, end=e, source="yahoo", interval="1d"))
    off = d1[d1["symbol"] == INDEX]
    if off.empty:
        raise RuntimeError(f"no official XU030 daily close for {calib_date}")
    ref_ts = off["market_ts"].iloc[0]
    return calibrate_divisor(
        d1[d1["symbol"] != INDEX], cons_all, off, ref_ts,
        official_source="yahoo 1d XU030.IS close (== BIST official close, checked vs PayEndeksleri)",
        constituents_version=constituents_version(), max_staleness_s=None)


# --------------------------------------------------------------------------- EOD test
def eod_backtest(state0: DivisorState, cons_all: list[Constituent], actions: pd.DataFrame,
                 calib_date: date, end_date: date) -> pd.DataFrame:
    """Daily close replication, out-of-sample after calib_date."""
    from bist30.engine import active_constituents, apply_index_events, raw_mcap
    from bist30.sources import store

    syms = sorted({c.symbol for c in cons_all} | set(actions["symbol"]))
    s, _ = _ist_day_bounds(calib_date)
    _, e = _ist_day_bounds(end_date)
    d1 = store.read_bars(syms + [INDEX], start=s, end=e, source="yahoo", interval="1d")
    d1["day"] = _ist_date(d1["market_ts"])
    close = d1.pivot_table(index="day", columns="symbol", values="close", aggfunc="last").sort_index()
    vol = d1.pivot_table(index="day", columns="symbol", values="volume", aggfunc="last").reindex(close.index)

    bist = store.read_bars(syms + [INDEX], start=s, end=e, source="borsaistanbul", interval="1d")
    bist["day"] = _ist_date(bist["market_ts"])
    bist_close = bist.pivot_table(index="day", columns="symbol", values="close", aggfunc="last")

    state = copy.deepcopy(state0)
    cons = active_constituents(cons_all, calib_date)
    rows = []
    days = [d for d in close.index if calib_date < d <= end_date]
    prev = calib_date
    for d in days:
        acts = actions[actions["effective_date"] == d]
        new = active_constituents(cons_all, d)
        notes = []
        if len(acts) or _defn(new) != _defn(cons):
            state, cons, _, notes = apply_index_events(
                state, cons, close.loc[prev].to_dict(), d, actions=acts if len(acts) else None,
                new_constituents=new, reason=f"EOD {d}")
        px = close.loc[d]
        calc = raw_mcap(cons, px.to_dict()) / state.divisor
        off = float(px.get(INDEX, np.nan))
        zero_vol = [c.symbol for c in cons if not (vol.loc[d].get(c.symbol, np.nan) > 0)]
        # Cross-check the price source against Borsa Istanbul's official bulletin where available
        max_px_dev = np.nan
        if d in bist_close.index:
            b = bist_close.loc[d]
            devs = [abs(px[c.symbol] / b[c.symbol] - 1) for c in cons
                    if c.symbol in b and np.isfinite(b[c.symbol]) and np.isfinite(px.get(c.symbol, np.nan))]
            max_px_dev = max(devs) * 100 if devs else np.nan
        rows.append({
            "date": d, "calc_close": round(calc, 2), "official_close": off,
            "official_bist": float(bist_close.loc[d, INDEX]) if d in bist_close.index and INDEX in bist_close else np.nan,
            "diff_pts": calc - off, "diff_pct": (calc / off - 1) * 100,
            "divisor": state.divisor, "n_components": len(cons),
            "zero_volume_symbols": ";".join(zero_vol),
            "max_price_dev_vs_bist_pct": max_px_dev, "events": " | ".join(notes)})
        prev = d
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- intraday test
def intraday_backtest(state0: DivisorState, cons_all: list[Constituent], actions: pd.DataFrame,
                      calib_date: date, end_date: date, staleness_s: float = 300) -> pd.DataFrame:
    from bist30.engine import compute_index_series
    from bist30.sources import store

    syms = sorted({c.symbol for c in cons_all} | set(actions["symbol"]))
    s, _ = _ist_day_bounds(calib_date + timedelta(days=1))
    _, e = _ist_day_bounds(end_date)
    bars = store.read_bars(syms + [INDEX], start=s, end=e, source="yahoo", interval="1m")
    eq, off = bars[bars["symbol"] != INDEX], bars[bars["symbol"] == INDEX]
    # Official closes (Yahoo 1d == BIST bulletin) as the day base price: Yahoo 1m lacks the
    # closing-auction print, so the last 1m price is not the price-limit / rule-5.1 base.
    s0, _ = _ist_day_bounds(calib_date)
    closes = _daily_closes(store.read_bars(syms, start=s0, end=e, source="yahoo", interval="1d"))
    return compute_index_series(eq, cons_all, copy.deepcopy(state0), official=off,
                                staleness_s=staleness_s, actions=actions, daily_closes=closes)


# --------------------------------------------------------------------------- VIOP
def viop_analysis(index_result: pd.DataFrame, day: date, codes: Optional[list[str]] = None) -> dict:
    """Basis of each Foreks-polled contract vs computed & official spot (both delayed_15m)."""
    from bist30.analysis import basis_frame, basis_summary, contract_from_code
    from bist30.sources import store
    from bist30.sources.foreks import bars_from_quotes
    from bist30.sources.rates import get_short_rate

    s, e = _ist_day_bounds(day)
    q = store.read_quotes(start=s, end=e, source="foreks", dedupe_market_ts=True)
    out = {"n_quotes": int(len(q)), "contracts": {}}
    if q.empty:
        out["status"] = "not feasible: no VIOP quotes collected"
        return out
    try:
        rate = get_short_rate()
        out["rate"] = {"value": rate.rate, "date": str(rate.as_of), "source": rate.source}
        r = rate.rate
    except Exception as ex:  # no guessed rate
        out["rate"] = {"error": str(ex)}
        r = None
    fut_bars = bars_from_quotes(q)
    spot = index_result[_ist_date(index_result["market_ts"]) == day]
    for code in codes or sorted(fut_bars["symbol"].unique()):
        try:
            c = contract_from_code(code)
            bf = basis_frame(spot, fut_bars, c, r, on_latency_mismatch="flag")
            out["contracts"][code] = {"frame": bf, "summary": basis_summary(bf)}
        except Exception as ex:
            out["contracts"][code] = {"error": f"{type(ex).__name__}: {ex}"}
    return out


# --------------------------------------------------------------------------- run all
def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items() if not isinstance(v, pd.DataFrame)}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (pd.Timestamp, datetime, date)):
        return str(x)
    return x


def _rel(path) -> str:
    """Repo-relative path for reports/JSON (no machine-specific absolute paths)."""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def run_all(calib_date: date = date(2026, 10, 1), end_date: Optional[date] = None,
            staleness_s: float = 300, make_plots: bool = True) -> dict:
    from bist30.analysis import (delay_stats, plot_basis, plot_error_hist, plot_intraday,
                                 replication_metrics, standard_feasibility, build_report)
    from bist30.engine import lag_diagnostics, replication_error_summary, save_divisor_state
    from bist30.metadata import load_corporate_actions
    from bist30.sources import store

    cfg = load_settings()
    end_date = end_date or pd.Timestamp.now(tz=ISTANBUL_TZ).date()
    res_dir = ROOT / cfg["data"]["results_dir"]
    fig_dir = ROOT / cfg["data"]["figures_dir"]
    res_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)

    cons_all = all_constituents()
    actions = _price_actions(load_corporate_actions(), calib_date)

    log.info("calibrating divisor once on %s close", calib_date)
    state0 = calibrate(calib_date, cons_all)
    save_divisor_state(state0)

    log.info("EOD backtest %s..%s", calib_date, end_date)
    eod = eod_backtest(state0, cons_all, actions, calib_date, end_date)
    eod.to_csv(res_dir / "eod_backtest.csv", index=False)

    log.info("intraday 1m backtest")
    res = intraday_backtest(state0, cons_all, actions, calib_date, end_date, staleness_s)
    final_state = res.attrs.get("divisor_state")
    res.to_csv(res_dir / "backtest_index_1m.csv", index=False)

    summary = replication_error_summary(res)
    metrics = replication_metrics(res)
    lag = lag_diagnostics(res)
    bars_today = store.read_bars([INDEX], start=_ist_day_bounds(end_date)[0], end=_ist_day_bounds(end_date)[1],
                                 source="yahoo", interval="1m", include_first_received=True)
    quotes_today = store.read_quotes(start=_ist_day_bounds(end_date)[0], end=_ist_day_bounds(end_date)[1])
    delay = pd.concat([delay_stats(bars_today), delay_stats(quotes_today)], ignore_index=True)

    viop = viop_analysis(res, end_date)

    figures = {}
    if make_plots:
        figures["intraday_all"] = _rel(plot_intraday(res, fig_dir / "intraday_all.png",
                                                    title="XU030: hesaplanan vs resmi (1 dk, tüm dönem)"))
        days = sorted(set(_ist_date(res["market_ts"])))
        for d in days[-3:]:
            figures[f"intraday_{d}"] = _rel(plot_intraday(res, fig_dir / f"intraday_{d}.png", day=d))
        figures["error_hist"] = _rel(plot_error_hist(res, fig_dir / "error_hist.png"))
        for code, v in viop["contracts"].items():
            bf = v.get("frame")
            if bf is not None and bf.attrs.get("comparison_valid", True) and bf["valid"].any():
                figures[f"basis_{code}"] = _rel(plot_basis(bf, fig_dir / f"basis_{code}.png"))

    have_fut = viop.get("n_quotes", 0) > 0
    have_rate = "value" in viop.get("rate", {})
    exps = standard_feasibility(have_spot_intraday=True, spot_latency="delayed_15m",
                                have_futures=have_fut, futures_latency="delayed_15m" if have_fut else None,
                                have_rate=have_rate)
    front = next((v["summary"] for k, v in viop["contracts"].items() if k == "F_XU0301226" and "summary" in v), None)
    eod_ok = eod.dropna(subset=["diff_pts"])
    notes = [
        f"Bölen bir kez kalibre edildi: {calib_date} resmi kapanış, B={state0.divisor:.8f}.",
        f"Gün sonu (EOD) testi {len(eod_ok)} gün: MAE {eod_ok['diff_pts'].abs().mean():.2f} puan, "
        f"ortalama mutlak %{eod_ok['diff_pct'].abs().mean():.4f}, maks %{eod_ok['diff_pct'].abs().max():.4f}.",
        "Yahoo 1 dk verisinde kapanış seansı fiyatı yok (son bar 17:59); EOD testi günlük kapanışla yapılır.",
        "Tüm gün içi veriler 15 dk gecikmeli (delayed_15m); gerçek zamanlı veriyle karıştırılmadı.",
    ]
    build_report(metrics, basis_summary=front, experiments=exps, figures=figures, delay=delay,
                 notes=notes, out_path=ROOT / "reports" / "summary.md")

    out = {"calibration": asdict(state0), "final_divisor_state": asdict(final_state) if final_state else None,
           "eod": eod, "intraday_summary": summary, "metrics": metrics, "lag": lag,
           "delay": delay, "viop": viop, "figures": figures, "result": res}
    with open(res_dir / "run_summary.json", "w", encoding="utf-8") as f:
        json.dump(_jsonable({k: v for k, v in out.items() if k not in ("result", "eod", "delay", "lag")}
                            | {"lag_best": lag.attrs.get("best_lag")}), f, ensure_ascii=False, indent=2)
    return out
