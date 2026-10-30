"""Order-book features and the "does imbalance predict the next mid change" harness.

STATUS: requires free, permitted depth data. Borsa İstanbul / VİOP depth (market
by price) is a licensed data-vendor product; no free source was identified, so these
functions are implemented and tested on synthetic snapshots only.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from bist30.models import OrderBookSnapshot

from ._common import Feasibility, latency_classes, to_utc

DEPTH_STATUS = "requires free depth data (no free, permitted BIST/VİOP depth source identified)"


def _levels(side: Sequence, n: int) -> tuple[np.ndarray, np.ndarray]:
    lv = list(side)[:n]
    px = np.array([float(p) for p, _ in lv] + [np.nan] * (n - len(lv)))
    sz = np.array([float(q) for _, q in lv] + [0.0] * (n - len(lv)))
    return px, sz


def orderbook_features(snapshots: Iterable[OrderBookSnapshot] | pd.DataFrame,
                       levels: Sequence[int] = (1, 3, 5)) -> pd.DataFrame:
    """Per-snapshot features.

    imbalance_lN = (sum bid size - sum ask size) / (sum bid + sum ask) over the best N
    levels (in [-1, 1]; >0 = bid-heavy). microprice = (ask*bid_size + bid*ask_size) /
    (bid_size + ask_size) on L1. spread = ask - bid; spread_bps relative to mid.
    Crossed/locked or one-sided books are kept with ``book_ok=False``.
    """
    if isinstance(snapshots, pd.DataFrame):
        snaps = [OrderBookSnapshot(**r) for r in snapshots.to_dict("records")]
    else:
        snaps = list(snapshots)
    nmax = max(levels)
    rows = []
    for s in snaps:
        bp, bs = _levels(s.bids, nmax)
        ap, as_ = _levels(s.asks, nmax)
        bid, ask, bq, aq = bp[0], ap[0], bs[0], as_[0]
        ok = np.isfinite(bid) and np.isfinite(ask) and ask > bid and bq > 0 and aq > 0
        mid = (bid + ask) / 2 if np.isfinite(bid) and np.isfinite(ask) else np.nan
        row = {
            "symbol": s.symbol, "market_ts": s.market_ts, "received_ts": s.received_ts,
            "source": s.source, "latency_class": getattr(s.latency_class, "value", s.latency_class),
            "best_bid": bid, "best_ask": ask, "bid_size_1": bq, "ask_size_1": aq,
            "mid": mid, "spread": ask - bid,
            "spread_bps": (ask - bid) / mid * 1e4 if np.isfinite(mid) and mid else np.nan,
            "microprice": (ask * bq + bid * aq) / (bq + aq) if (bq + aq) > 0 and ok else np.nan,
            "book_ok": bool(ok),
        }
        for n in levels:
            b, a = bs[:n].sum(), as_[:n].sum()
            row[f"imbalance_l{n}"] = (b - a) / (b + a) if (b + a) > 0 else np.nan
        # depth-weighted imbalance with 1/level weights over all levels
        w = 1.0 / np.arange(1, nmax + 1)
        b, a = (bs * w).sum(), (as_ * w).sum()
        row["imbalance_weighted"] = (b - a) / (b + a) if (b + a) > 0 else np.nan
        row["microprice_minus_mid"] = row["microprice"] - mid
        rows.append(row)
    df = pd.DataFrame(rows)
    if len(df):
        df["market_ts"] = to_utc(df["market_ts"])
        df = df.sort_values(["symbol", "market_ts"]).reset_index(drop=True)
    return df


def imbalance_predictive_eval(features: pd.DataFrame, feature: str = "imbalance_l1", horizon: int = 1,
                              n_bins: int = 5, min_obs: int = 200) -> Feasibility:
    """Does ``feature`` at snapshot t predict mid(t+h) - mid(t)?

    Reports Pearson/Spearman correlation, OLS slope with t-stat (HAC not applied;
    overlapping horizons > 1 inflate t), sign hit-rate on non-zero moves, and the
    mean next mid change per feature quantile bin.
    """
    name = "orderbook_imbalance_prediction"
    if features is None or len(features) == 0:
        return Feasibility.no(name, DEPTH_STATUS)
    lats = latency_classes(features)
    if len(lats) != 1:
        return Feasibility.no(name, f"mixed/missing latency classes {sorted(lats)}")
    df = features[features["book_ok"]].copy()
    df["dmid"] = df.groupby("symbol")["mid"].shift(-horizon) - df["mid"]
    d = df[[feature, "dmid"]].dropna()
    if len(d) < min_obs:
        return Feasibility.no(name, f"insufficient observations ({len(d)} < {min_obs})")
    x, y = d[feature].to_numpy(float), d["dmid"].to_numpy(float)
    if np.std(x) == 0 or np.std(y) == 0:
        return Feasibility.no(name, "zero variance in feature or mid change")
    X = np.column_stack([np.ones(len(x)), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X @ beta
    s2 = res @ res / (len(y) - 2)
    se = np.sqrt(s2 * np.linalg.inv(X.T @ X)[1, 1])
    nz = y != 0
    hit = float((np.sign(x[nz]) == np.sign(y[nz])).mean()) if nz.any() else float("nan")
    bins = pd.qcut(d[feature], q=n_bins, duplicates="drop")
    by_bin = d.groupby(bins, observed=True)["dmid"].agg(["mean", "count"]).reset_index()
    by_bin[feature] = by_bin[feature].astype(str)
    result = {
        "n": int(len(d)), "horizon": horizon, "feature": feature,
        "pearson": float(np.corrcoef(x, y)[0, 1]),
        "spearman": float(pd.Series(x).rank().corr(pd.Series(y).rank())),
        "slope": float(beta[1]), "slope_t": float(beta[1] / se),
        "r2": float(1 - (res @ res) / ((y - y.mean()) @ (y - y.mean()))),
        "hit_rate_nonzero": hit, "share_nonzero_moves": float(nz.mean()),
        "by_bin": by_bin,
    }
    return Feasibility(name=name, feasible=True, result=result,
                       details={"latency_class": next(iter(lats))})
