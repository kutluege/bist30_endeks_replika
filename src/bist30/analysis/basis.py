"""Basis analytics: VİOP futures vs official and computed XU030 at the same market_ts."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from bist30.models import FuturesContract

from ._common import LatencyMismatchError, check_same_latency, latency_classes, to_utc
from .contracts import DEFAULT_CALENDAR, MarketCalendar
from .fair_value import align_rate, dividend_fv, implied_rate, theoretical_futures, year_fraction

BASIS_COLUMNS = [
    "market_ts",              # futures bar end (UTC)
    "spot_ts",                # matched index_result market_ts
    "align_lag_s",            # market_ts - spot_ts (0 for exact match)
    "futures_price",
    "spot_official",
    "spot_calc",
    "tau_years",
    "rate",
    "fair_value_official",    # theoretical futures from official spot
    "fair_value_calc",        # theoretical futures from computed spot
    "raw_basis_official",     # F - S_official
    "raw_basis_calc",         # F - S_calc
    "theo_basis_official",    # FV - S_official
    "residual_official",      # F - FV(official)   (carry-adjusted mispricing, points)
    "residual_calc",          # F - FV(calc)
    "residual_official_bps",  # residual / S_official * 1e4
    "implied_repo_official",  # annualised, in `compounding`
    "implied_repo_calc",
    "spot_calc_minus_official",
    "residual_calc_minus_official",
    "valid",                  # official-based analytics valid
    "calc_valid",             # computed-spot analytics valid
    "invalid_reason",
    "latency_class",
]


def _futures_rows(futures_bars: pd.DataFrame, contract: FuturesContract, price_col: str) -> pd.DataFrame:
    f = futures_bars
    if "symbol" in f.columns:
        f = f[f["symbol"].astype(str) == contract.code]
    if len(f) == 0:
        raise ValueError(f"no futures bars for {contract.code}")
    out = pd.DataFrame({
        "market_ts": to_utc(f["market_ts"]).to_numpy(),
        "futures_price": f[price_col].astype(float).to_numpy(),
    })
    out["market_ts"] = to_utc(out["market_ts"])
    return out.drop_duplicates("market_ts", keep="last").sort_values("market_ts").reset_index(drop=True)


def basis_frame(index_result: pd.DataFrame, futures_bars: pd.DataFrame, contract: FuturesContract,
                rate, *, div_yield: float = 0.0, dividends=None, compounding: str = "simple",
                day_count: str = "ACT/365", tolerance: str | pd.Timedelta = "0s",
                on_latency_mismatch: str = "raise", price_col: str = "close",
                calendar: MarketCalendar = DEFAULT_CALENDAR) -> pd.DataFrame:
    """Align futures with official AND computed spot and compute basis analytics.

    Alignment: exact ``market_ts`` match when ``tolerance == 0`` (default); otherwise
    as-of backward (spot at or before the futures bar end, within ``tolerance``).
    Latency classes of futures and index_result must be identical and known. On
    mismatch: ``on_latency_mismatch="raise"`` -> LatencyMismatchError (default);
    ``"flag"`` -> frame with ALL analytics NaN, ``valid=False`` and the reason
    (``df.attrs["comparison_valid"] = False``). No numbers are produced across
    latency classes.
    """
    fut = _futures_rows(futures_bars, contract, price_col)
    reason = check_same_latency(futures_bars if "symbol" not in futures_bars.columns
                                else futures_bars[futures_bars["symbol"].astype(str) == contract.code],
                                index_result, "futures", "spot")
    if reason is not None:
        if on_latency_mismatch == "raise":
            raise LatencyMismatchError(reason)
        out = pd.DataFrame({c: np.nan for c in BASIS_COLUMNS}, index=range(len(fut)))
        out["market_ts"] = fut["market_ts"]
        out["futures_price"] = fut["futures_price"]
        out["valid"] = False
        out["calc_valid"] = False
        out["invalid_reason"] = reason
        out["latency_class"] = ";".join(sorted(latency_classes(futures_bars) | latency_classes(index_result)))
        out.attrs.update(comparison_valid=False, reason=reason, contract=contract.code)
        return out[BASIS_COLUMNS]

    lat = next(iter(latency_classes(index_result)))
    ir = index_result.copy()
    ir["spot_ts"] = to_utc(ir["market_ts"])
    calc = ir["calc_value"].astype(float)
    if "valid" in ir.columns:
        calc = calc.where(ir["valid"].astype(bool))
    spot = pd.DataFrame({"spot_ts": ir["spot_ts"], "spot_official": ir["official_value"].astype(float),
                         "spot_calc": calc}).drop_duplicates("spot_ts", keep="last").sort_values("spot_ts")

    tol = pd.Timedelta(tolerance)
    if tol == pd.Timedelta(0):
        m = fut.merge(spot, left_on="market_ts", right_on="spot_ts", how="left")
    else:
        m = pd.merge_asof(fut, spot, left_on="market_ts", right_on="spot_ts",
                          direction="backward", tolerance=tol)
    m["align_lag_s"] = (m["market_ts"] - m["spot_ts"]).dt.total_seconds()

    idx = pd.DatetimeIndex(m["market_ts"])
    tau = year_fraction(idx, contract, day_count, calendar)
    r = align_rate(rate, idx)
    m["tau_years"] = tau
    m["rate"] = r
    F = m["futures_price"].to_numpy(dtype=float)
    So = m["spot_official"].to_numpy(dtype=float)
    Sc = m["spot_calc"].to_numpy(dtype=float)
    kw = dict(div_yield=div_yield, dividends=dividends, compounding=compounding,
              day_count=day_count, calendar=calendar)
    rate_s = pd.Series(r, index=idx)
    fv_o = theoretical_futures(pd.Series(So, index=idx), contract, rate_s, **kw).to_numpy()
    fv_c = theoretical_futures(pd.Series(Sc, index=idx), contract, rate_s, **kw).to_numpy()
    m["fair_value_official"] = fv_o
    m["fair_value_calc"] = fv_c
    m["raw_basis_official"] = F - So
    m["raw_basis_calc"] = F - Sc
    m["theo_basis_official"] = fv_o - So
    m["residual_official"] = F - fv_o
    m["residual_calc"] = F - fv_c
    m["residual_official_bps"] = (F - fv_o) / So * 1e4
    # implied repo: dividends future-valued at the input rate (0 if unknown -> approximation)
    dfv = dividend_fv(idx, contract, np.nan_to_num(r, nan=0.0), dividends, compounding)
    m["implied_repo_official"] = implied_rate(F, So, tau, dfv, div_yield, compounding)
    m["implied_repo_calc"] = implied_rate(F, Sc, tau, dfv, div_yield, compounding)
    m["spot_calc_minus_official"] = Sc - So
    m["residual_calc_minus_official"] = m["residual_calc"] - m["residual_official"]

    reasons = []
    valid = []
    calc_valid = []
    for f, so, sc, t, rr, st in zip(F, So, Sc, tau, r, m["spot_ts"]):
        rs = []
        if not np.isfinite(f):
            rs.append("no_futures_price")
        if pd.isna(st):
            rs.append("no_spot_at_market_ts")
        elif not np.isfinite(so):
            rs.append("no_official_spot")
        if not np.isfinite(t):
            rs.append("expired")
        if not np.isfinite(rr):
            rs.append("no_rate")
        ok = not rs
        cv = np.isfinite(f) and np.isfinite(sc) and np.isfinite(t) and np.isfinite(rr)
        if not np.isfinite(sc) and not pd.isna(st):
            rs.append("calc_invalid")
        valid.append(ok)
        calc_valid.append(bool(cv))
        reasons.append(";".join(rs))
    m["valid"] = valid
    m["calc_valid"] = calc_valid
    m["invalid_reason"] = reasons
    m["latency_class"] = lat
    out = m[BASIS_COLUMNS].copy()
    out.attrs.update(comparison_valid=True, contract=contract.code, compounding=compounding,
                     day_count=day_count, tolerance=str(tol), latency_class=lat)
    return out


def basis_summary(bf: pd.DataFrame) -> dict:
    """Summary statistics of a basis frame (valid rows only)."""
    if not bf.attrs.get("comparison_valid", True):
        return {"status": f"not feasible: {bf.attrs.get('reason', 'invalid comparison')}"}
    v = bf[bf["valid"]]
    if len(v) == 0:
        return {"status": "not feasible: no valid aligned observations",
                "n": int(len(bf)), "invalid_reasons": bf["invalid_reason"].value_counts().to_dict()}
    def st(col):
        x = v[col].astype(float).dropna()
        return {"mean": float(x.mean()), "std": float(x.std()), "min": float(x.min()),
                "max": float(x.max()), "n": int(len(x))} if len(x) else None
    return {
        "status": "ok",
        "n": int(len(bf)), "n_valid": int(len(v)),
        "raw_basis_official": st("raw_basis_official"),
        "residual_official": st("residual_official"),
        "residual_official_bps": st("residual_official_bps"),
        "implied_repo_official": st("implied_repo_official"),
        "residual_calc_minus_official": st("residual_calc_minus_official"),
        "invalid_reasons": bf.loc[~bf["valid"], "invalid_reason"].value_counts().to_dict(),
    }
