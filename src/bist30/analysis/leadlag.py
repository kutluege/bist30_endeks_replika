"""Lead-lag / price-discovery experiments between VİOP futures and XU030 spot.

Every entry point returns a :class:`Feasibility`. When latency classes differ or are
unknown, sampling is too coarse, or there are too few overlapping returns, the
result is ``feasible=False`` with the reason and NO numbers.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ._common import Feasibility, check_same_latency, interval_seconds, istanbul_date, to_utc

NAME = "lead_lag"


def _frame_series(obj: pd.DataFrame, value_col: str, symbol: Optional[str]) -> pd.DataFrame:
    df = obj
    if symbol is not None and "symbol" in df.columns:
        df = df[df["symbol"].astype(str) == symbol]
    out = pd.DataFrame({"market_ts": to_utc(df["market_ts"]).to_numpy(),
                        "v": df[value_col].astype(float).to_numpy()})
    out["market_ts"] = to_utc(out["market_ts"])
    return out.dropna().drop_duplicates("market_ts", keep="last").sort_values("market_ts")


def _sampling_s(df: pd.DataFrame, frame: pd.DataFrame) -> float:
    if "interval" in frame.columns and frame["interval"].notna().any():
        vals = {interval_seconds(x) for x in frame["interval"].dropna().unique()}
        vals = {x for x in vals if np.isfinite(x)}
        if vals:
            return max(vals)
    d = df["market_ts"].diff().dt.total_seconds()
    d = d[d > 0]
    return float(d.median()) if len(d) else float("nan")


def _aligned_returns(fut: pd.DataFrame, spot: pd.DataFrame, step_s: float) -> pd.DataFrame:
    m = fut.merge(spot, on="market_ts", suffixes=("_f", "_s"))
    m["day"] = istanbul_date(m["market_ts"])
    m["lf"] = np.log(m["v_f"])
    m["ls"] = np.log(m["v_s"])
    gap = m.groupby("day")["market_ts"].diff().dt.total_seconds()
    contiguous = np.isclose(gap, step_s) if step_s > 0 else gap.notna()
    m["rf"] = np.where(contiguous, m.groupby("day")["lf"].diff(), np.nan)
    m["rs"] = np.where(contiguous, m.groupby("day")["ls"].diff(), np.nan)
    return m


def cross_correlation(m: pd.DataFrame, max_lag: int) -> pd.DataFrame:
    """corr(r_fut[t], r_spot[t+k]) for k in [-max_lag, max_lag] (same day only).
    k > 0: futures moves first (futures lead); k < 0: spot leads."""
    rows = []
    for k in range(-max_lag, max_lag + 1):
        rs_shift = m.groupby("day")["rs"].shift(-k)
        pair = pd.concat([m["rf"], rs_shift], axis=1).dropna()
        n = len(pair)
        c = float(pair.iloc[:, 0].corr(pair.iloc[:, 1])) if n > 2 else float("nan")
        rows.append({"lag": k, "corr": c, "n": n, "ci95": 1.96 / np.sqrt(n) if n > 0 else np.nan})
    return pd.DataFrame(rows)


def price_discovery_vecm(m: pd.DataFrame, p: int = 2) -> dict:
    """Bivariate VECM on log prices with cointegrating vector (1,-1) and a per-day
    demeaned basis (absorbs carry). Returns Gonzalo-Granger component shares and
    Hasbrouck information-share bounds (both Cholesky orderings)."""
    d = m.copy()
    d["z"] = d["lf"] - d["ls"]
    d["z"] = d["z"] - d.groupby("day")["z"].transform("mean")
    cols = {"z1": d.groupby("day")["z"].shift(1)}
    for j in range(1, p + 1):
        cols[f"rf{j}"] = d.groupby("day")["rf"].shift(j)
        cols[f"rs{j}"] = d.groupby("day")["rs"].shift(j)
    X = pd.DataFrame(cols)
    Y = d[["rf", "rs"]]
    ok = X.notna().all(axis=1) & Y.notna().all(axis=1)
    X, Y = X[ok], Y[ok]
    if len(X) < 50:
        return {"status": f"not feasible: only {len(X)} usable VECM rows"}
    Xm = np.column_stack([np.ones(len(X)), X.to_numpy()])
    B, *_ = np.linalg.lstsq(Xm, Y.to_numpy(), rcond=None)
    resid = Y.to_numpy() - Xm @ B
    a_f, a_s = float(B[1, 0]), float(B[1, 1])
    omega = np.cov(resid.T)
    denom = a_s - a_f
    if not np.isfinite(denom) or abs(denom) < 1e-12:
        return {"status": "not feasible: no error correction detected (alpha_s - alpha_f ~ 0)",
                "alpha_fut": a_f, "alpha_spot": a_s}
    gamma = np.array([a_s / denom, -a_f / denom])  # component shares (futures, spot)
    var = float(gamma @ omega @ gamma)
    is_bounds = {}
    for name, order in (("fut_first", [0, 1]), ("spot_first", [1, 0])):
        om = omega[np.ix_(order, order)]
        L = np.linalg.cholesky(om)
        g = gamma[order] @ L
        share = g ** 2 / var
        is_bounds[name] = dict(zip(np.array(["fut", "spot"])[order], share.tolist()))
    is_f = sorted([is_bounds["fut_first"]["fut"], is_bounds["spot_first"]["fut"]])
    warn = "" if (a_f <= 0 <= a_s) else "alpha signs not error-correcting; shares unreliable"
    return {
        "status": "ok",
        "n": int(len(X)), "lags": p,
        "alpha_fut": a_f, "alpha_spot": a_s,
        "component_share_fut": float(gamma[0]), "component_share_spot": float(gamma[1]),
        "info_share_fut_lower": float(is_f[0]), "info_share_fut_upper": float(is_f[1]),
        "info_share_fut_mid": float(np.mean(is_f)),
        "warning": warn,
    }


def lead_lag_analysis(futures: pd.DataFrame, spot: pd.DataFrame, *, futures_symbol: Optional[str] = None,
                      spot_col: str = "official_value", futures_col: str = "close", max_lag: int = 10,
                      min_obs: int = 300, max_sampling_s: float = 60.0, max_zero_frac: float = 0.5,
                      vecm_lags: int = 2) -> Feasibility:
    """Cross-correlation of futures vs spot returns and price-discovery-lite.

    ``futures``: bars frame (BAR_COLUMNS). ``spot``: index_result frame (use
    ``spot_col="official_value"`` or ``"calc_value"``) or a bars frame (``"close"``).
    Refuses (feasible=False) if latency classes differ/unknown, sampling is coarser
    than ``max_sampling_s``, overlapping returns < ``min_obs`` or either series is
    mostly stale (zero returns > ``max_zero_frac``).
    """
    if futures is None or len(futures) == 0:
        return Feasibility.no(NAME, "no futures data (no free, permitted VİOP intraday source)")
    if spot is None or len(spot) == 0:
        return Feasibility.no(NAME, "no spot data")
    fsub = futures if futures_symbol is None or "symbol" not in futures.columns \
        else futures[futures["symbol"].astype(str) == futures_symbol]
    reason = check_same_latency(fsub, spot, "futures", "spot")
    if reason:
        return Feasibility.no(NAME, reason)
    if spot_col not in spot.columns:
        return Feasibility.no(NAME, f"spot column {spot_col!r} missing")
    f = _frame_series(fsub, futures_col, None)
    s = _frame_series(spot, spot_col, None)
    sf, ss = _sampling_s(f, fsub), _sampling_s(s, spot)
    step = max(sf, ss)
    if not np.isfinite(step):
        return Feasibility.no(NAME, "cannot determine sampling frequency")
    if step > max_sampling_s:
        return Feasibility.no(NAME, f"sampling too coarse ({step:.0f}s > {max_sampling_s:.0f}s); "
                                    "lead-lag at index/futures horizons needs <= 1-minute data",
                              futures_sampling_s=sf, spot_sampling_s=ss)
    m = _aligned_returns(f, s, step)
    both = m[["rf", "rs"]].dropna()
    if len(both) < min_obs:
        return Feasibility.no(NAME, f"insufficient overlapping returns ({len(both)} < {min_obs})",
                              n_overlap=int(len(both)))
    zf, zs = float((both["rf"] == 0).mean()), float((both["rs"] == 0).mean())
    if max(zf, zs) > max_zero_frac:
        return Feasibility.no(NAME, f"series too stale (zero-return share futures={zf:.0%}, spot={zs:.0%})")
    ccf = cross_correlation(m, max_lag)
    pos = ccf[ccf["lag"] > 0]["corr"].pow(2).sum()
    neg = ccf[ccf["lag"] < 0]["corr"].pow(2).sum()
    best = ccf.loc[ccf["corr"].abs().idxmax()]
    sig = ccf[(ccf["lag"] != 0) & (ccf["corr"].abs() > ccf["ci95"])]
    summary = {
        "n_returns": int(len(both)), "sampling_s": step,
        "corr_lag0": float(ccf.loc[ccf["lag"] == 0, "corr"].iloc[0]),
        "best_lag": int(best["lag"]), "best_corr": float(best["corr"]),
        "lead_ratio_fut_over_spot": float(pos / neg) if neg > 0 else float("inf"),
        "significant_lags": sig["lag"].astype(int).tolist(),
        "zero_return_share": {"futures": zf, "spot": zs},
    }
    pd_res = price_discovery_vecm(m, vecm_lags)
    return Feasibility(name=NAME, feasible=True, result={"ccf": ccf, "summary": summary,
                                                         "price_discovery": pd_res})
