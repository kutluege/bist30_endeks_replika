"""Replication (validation) metrics: computed XU030 vs official XU030."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ._common import istanbul_date, latency_classes, to_utc


def _err_stats(e_pts: pd.Series, e_pct: pd.Series) -> dict:
    a, ap = e_pts.abs(), e_pct.abs()
    return {
        "n": int(len(e_pts)),
        "mae_pts": float(a.mean()),
        "rmse_pts": float(np.sqrt((e_pts ** 2).mean())),
        "median_abs_pts": float(a.median()),
        "p95_abs_pts": float(a.quantile(0.95)),
        "max_abs_pts": float(a.max()),
        "bias_pts": float(e_pts.mean()),
        "mae_pct": float(ap.mean()),
        "rmse_pct": float(np.sqrt((e_pct ** 2).mean())),
        "median_abs_pct": float(ap.median()),
        "p95_abs_pct": float(ap.quantile(0.95)),
        "max_abs_pct": float(ap.max()),
        "bias_pct": float(e_pct.mean()),
    }


def _returns_within_day(df: pd.DataFrame, col: str) -> pd.Series:
    """Log returns between consecutive observations of the same Istanbul day."""
    lr = np.log(df[col].astype(float))
    return lr.groupby(df["_day"]).diff()


def tracking_error(df: pd.DataFrame) -> dict:
    """Tracking error of intraday returns (calc vs official), consecutive valid
    observations of the same day only (no overnight returns)."""
    rc = _returns_within_day(df, "calc_value")
    ro = _returns_within_day(df, "official_value")
    d = (rc - ro).dropna()
    if len(d) < 2:
        return {"n_returns": int(len(d)), "te_per_obs_bps": float("nan"), "return_corr": float("nan")}
    both = pd.concat([rc, ro], axis=1).dropna()
    corr = float(both.iloc[:, 0].corr(both.iloc[:, 1])) if len(both) > 2 else float("nan")
    out = {
        "n_returns": int(len(d)),
        "te_per_obs_bps": float(d.std() * 1e4),
        "mean_return_diff_bps": float(d.mean() * 1e4),
        "return_corr": corr,
    }
    # daily (close-to-close) TE if several days
    last = df.groupby("_day").tail(1).set_index("_day")
    if len(last) >= 3:
        dc = np.log(last["calc_value"]).diff() - np.log(last["official_value"]).diff()
        dc = dc.dropna()
        out["te_daily_bps"] = float(dc.std() * 1e4)
        out["te_annualised_pct"] = float(dc.std() * np.sqrt(252) * 100)
    return out


def delay_stats(frame: pd.DataFrame, backfill_threshold_s: float = 3600.0,
                group_cols: tuple = ("source", "latency_class")) -> pd.DataFrame:
    """Data delay = received_ts - market_ts (uses ``first_received_ts`` if present,
    the first time the record was seen). Rows with delay > ``backfill_threshold_s``
    are counted as historical back-fill and excluded from the live statistics."""
    if frame is None or len(frame) == 0:
        return pd.DataFrame()
    rcol = "first_received_ts" if "first_received_ts" in frame.columns and frame["first_received_ts"].notna().any() \
        else "received_ts"
    if rcol not in frame.columns:
        return pd.DataFrame()
    df = frame.copy()
    df["_delay_s"] = (to_utc(df[rcol]) - to_utc(df["market_ts"])).dt.total_seconds()
    df = df[df["_delay_s"].notna()]
    gcols = [c for c in group_cols if c in df.columns]
    if not gcols:
        df["_all"] = "all"
        gcols = ["_all"]
    rows = []
    for key, g in df.groupby(gcols, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        live = g.loc[g["_delay_s"] <= backfill_threshold_s, "_delay_s"]
        row = dict(zip(gcols, key))
        row.update({
            "received_col": rcol,
            "n": int(len(g)),
            "n_live": int(len(live)),
            "n_backfill": int(len(g) - len(live)),
            "delay_min_s": float(live.min()) if len(live) else np.nan,
            "delay_median_s": float(live.median()) if len(live) else np.nan,
            "delay_p95_s": float(live.quantile(0.95)) if len(live) else np.nan,
            "delay_max_s": float(live.max()) if len(live) else np.nan,
        })
        rows.append(row)
    return pd.DataFrame(rows)


def replication_metrics(index_result: pd.DataFrame, bars: Optional[pd.DataFrame] = None,
                        backfill_threshold_s: float = 3600.0) -> dict:
    """Validation metrics of an engine result frame (INDEX_RESULT_COLUMNS).

    Error = calc_value - official_value at the same market_ts, on rows that are
    ``valid`` and have an official value. Returns a dict with overall error stats,
    tracking error, coverage, invalid-reason breakdown, staleness, data delay
    (from optional ``received_ts``/``first_received_ts`` columns in ``index_result``
    or from ``bars``) and a per-day DataFrame under ``"per_day"``.
    """
    lats = latency_classes(index_result)
    if len(lats) > 1:
        return {"status": f"not feasible: mixed latency classes {sorted(lats)} in one result frame"}
    n_total = int(len(index_result))
    if n_total == 0:
        return {"status": "not feasible: empty index_result"}
    df = index_result.copy()
    df["market_ts"] = to_utc(df["market_ts"])
    df = df.sort_values("market_ts")
    df["_day"] = istanbul_date(df["market_ts"])
    valid = df["valid"].astype(bool) if "valid" in df.columns else df["calc_value"].notna()
    has_off = df["official_value"].notna()
    ok = valid & has_off & df["calc_value"].notna()
    v = df[ok].copy()
    v["_e"] = v["calc_value"].astype(float) - v["official_value"].astype(float)
    v["_ep"] = v["_e"] / v["official_value"].astype(float) * 100

    out: dict = {
        "status": "ok" if len(v) else "not feasible: no valid observations with an official value",
        "latency_class": next(iter(lats)) if lats else "unknown",
        "price_source": ";".join(sorted(df["price_source"].dropna().astype(str).unique())) if "price_source" in df else "",
        "official_source": ";".join(sorted(df["official_source"].dropna().astype(str).unique())) if "official_source" in df else "",
        "start": df["market_ts"].iloc[0], "end": df["market_ts"].iloc[-1],
        "n_total": n_total,
        "n_valid": int(valid.sum()),
        "n_with_official": int(has_off.sum()),
        "n_compared": int(len(v)),
        "pct_valid": float(valid.mean() * 100),
        "pct_compared": float(len(v) / n_total * 100),
    }
    if len(v):
        out.update(_err_stats(v["_e"], v["_ep"]))
        out["tracking"] = tracking_error(v)
    # invalid reasons (split ';'-joined reasons)
    inv = df.loc[~valid, "invalid_reason"].fillna("").astype(str) if "invalid_reason" in df else pd.Series(dtype=str)
    reasons = inv.str.split(";").explode().str.strip()
    reasons = reasons[reasons != ""]
    out["invalid_reasons"] = reasons.str.split(":").str[0].value_counts().to_dict()
    out["n_valid_no_official"] = int((valid & ~has_off).sum())
    # staleness
    if "n_stale" in df.columns:
        ns = df["n_stale"].astype(float)
        ms = df["max_staleness_s"].astype(float) if "max_staleness_s" in df else pd.Series(dtype=float)
        out["staleness"] = {
            "pct_rows_with_stale": float((ns > 0).mean() * 100),
            "mean_n_stale": float(ns.mean()),
            "max_n_stale": float(ns.max()),
            "max_staleness_median_s": float(ms.median()) if len(ms.dropna()) else np.nan,
            "max_staleness_p95_s": float(ms.quantile(0.95)) if len(ms.dropna()) else np.nan,
            "max_staleness_max_s": float(ms.max()) if len(ms.dropna()) else np.nan,
        }
        if "stale_symbols" in df.columns:
            ss = df["stale_symbols"].fillna("").astype(str).str.split(";").explode().str.strip()
            out["staleness"]["most_stale_symbols"] = ss[ss != ""].value_counts().head(5).to_dict()
    # data delay
    dsrc = df if ("received_ts" in df.columns or "first_received_ts" in df.columns) else bars
    out["delay"] = delay_stats(dsrc, backfill_threshold_s) if dsrc is not None else pd.DataFrame()

    # per day
    rows = []
    for day, g in df.groupby("_day"):
        gv = v[v["_day"] == day]
        gvalid = valid.loc[g.index]
        row = {"date": day, "n_total": int(len(g)), "n_compared": int(len(gv)),
               "pct_valid": float(gvalid.mean() * 100)}
        if len(gv):
            st = _err_stats(gv["_e"], gv["_ep"])
            row.update({k: st[k] for k in ("mae_pts", "rmse_pts", "max_abs_pts", "bias_pts",
                                           "mae_pct", "max_abs_pct", "bias_pct")})
            te = tracking_error(gv)
            row["te_per_obs_bps"] = te["te_per_obs_bps"]
        rows.append(row)
    out["per_day"] = pd.DataFrame(rows)
    return out
