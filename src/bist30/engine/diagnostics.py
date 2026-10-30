"""Diagnostics for computed vs official series. DIAGNOSTIC ONLY: nothing here
is ever used to shift, fit or re-calibrate the computed index."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from bist30.models import ISTANBUL_TZ


def lag_diagnostics(result: pd.DataFrame, max_lag: int = 5, grid: str = "1min",
                    valid_only: bool = True) -> pd.DataFrame:
    """Cross-correlation of 1-grid log returns: corr(calc_ret(t), official_ret(t + k)).

    k > 0 => the official series moves k grid steps AFTER ours (we lead);
    k < 0 => official leads. Returns are only formed between consecutive grid
    points of the same Istanbul day where both values exist; shifts are in
    time (regular grid), not in rows. ``attrs["best_lag"]`` = argmax corr.
    """
    df = result.copy()
    if valid_only:
        df = df[df["valid"].astype(bool)]
    df = df[np.isfinite(df["calc_value"].astype(float)) & np.isfinite(df["official_value"].astype(float))]
    rows = []
    if df.empty:
        out = pd.DataFrame({"lag": range(-max_lag, max_lag + 1), "corr": np.nan, "n": 0})
        out.attrs["best_lag"] = None
        return out
    ts = pd.DatetimeIndex(pd.to_datetime(df["market_ts"], utc=True))
    s = pd.DataFrame({"calc": df["calc_value"].astype(float).to_numpy(),
                      "off": df["official_value"].astype(float).to_numpy()}, index=ts)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    parts = []
    for _, g in s.groupby(s.index.tz_convert(ISTANBUL_TZ).date):
        full = pd.date_range(g.index.min(), g.index.max(), freq=grid)
        g = g.reindex(full)
        r = np.log(g).diff()
        parts.append(r)
    r = pd.concat(parts)
    days = r.index.tz_convert(ISTANBUL_TZ).date
    for k in range(-max_lag, max_lag + 1):
        off_shift = r["off"].groupby(days).shift(-k)   # official at t+k
        pair = pd.concat([r["calc"], off_shift], axis=1).dropna()
        n = len(pair)
        c = float(pair.iloc[:, 0].corr(pair.iloc[:, 1])) if n > 2 else math.nan
        rows.append({"lag": k, "corr": c, "n": n})
    out = pd.DataFrame(rows)
    best = out.loc[out["corr"].idxmax()] if out["corr"].notna().any() else None
    out.attrs["best_lag"] = int(best["lag"]) if best is not None else None
    return out


def replication_error_summary(result: pd.DataFrame, after=None) -> dict:
    """Error statistics on rows with valid calc AND official at the same market_ts."""
    df = result
    if after is not None:
        df = df[pd.to_datetime(df["market_ts"], utc=True) > pd.Timestamp(after)]
    n_rows = len(df)
    m = df["valid"].astype(bool) & np.isfinite(df["official_value"].astype(float))
    d = df.loc[m, "diff_pts"].astype(float)
    p = df.loc[m, "diff_pct"].astype(float)
    cont = df["session_phase"] == "continuous" if "session_phase" in df.columns else pd.Series(True, index=df.index)
    stal = df.loc[df["valid"].astype(bool), "max_staleness_s"].astype(float)
    wst = df.loc[df["valid"].astype(bool), "weighted_staleness_s"].astype(float) \
        if "weighted_staleness_s" in df.columns else pd.Series(dtype=float)
    return {
        "n_rows": n_rows,
        "n_compared": int(m.sum()),
        "pct_valid": float(df["valid"].mean() * 100) if n_rows else math.nan,
        "pct_valid_continuous": float(df.loc[cont, "valid"].mean() * 100) if cont.any() else math.nan,
        "mae_pts": float(d.abs().mean()) if len(d) else math.nan,
        "rmse_pts": float(np.sqrt((d ** 2).mean())) if len(d) else math.nan,
        "bias_pts": float(d.mean()) if len(d) else math.nan,
        "max_abs_pts": float(d.abs().max()) if len(d) else math.nan,
        "mean_abs_pct": float(p.abs().mean()) if len(p) else math.nan,
        "max_abs_pct": float(p.abs().max()) if len(p) else math.nan,
        "bias_pct": float(p.mean()) if len(p) else math.nan,
        "median_max_staleness_s": float(stal.median()) if len(stal) else math.nan,
        "median_weighted_staleness_s": float(wst.replace(np.inf, np.nan).median()) if len(wst) else math.nan,
        "p95_weighted_staleness_s": float(wst.replace(np.inf, np.nan).quantile(0.95)) if len(wst) else math.nan,
    }
