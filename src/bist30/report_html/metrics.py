"""Replication metrics + 'is there a systematic deviation?' verdict for the HTML report.

Everything here is computed from the engine result frame (INDEX_RESULT_COLUMNS +
EXTRA_RESULT_COLUMNS). Thresholds are explicit and travel with the payload.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from bist30.engine import lag_diagnostics, replication_error_summary
from bist30.models import ISTANBUL_TZ

from .content import reason_key

DEFAULT_THRESHOLDS = {
    "warn_pct": 0.10,        # |fark| > %0,10 -> "dikkat"
    "crit_pct": 0.25,        # |fark| > %0,25 -> "sapma"
    "bias_t": 2.0,           # |t| >= 2  (~%95) -> statistically significant
    "bias_econ_pct": 0.05,   # |ortalama fark| >= %0,05 -> economically meaningful
    "drift_t": 2.0,          # slope t-stat threshold (intraday & cross-day)
    "staleness_s": 300.0,    # engine staleness rule
}


def _f(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _local(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts, utc=True).dt.tz_convert(ISTANBUL_TZ)


def oos_rows(results: pd.DataFrame, calibration_date: date) -> pd.DataFrame:
    """Rows strictly after the calibration trading day (out-of-sample)."""
    lt = _local(results["market_ts"])
    return results[(lt.dt.date > calibration_date).to_numpy()].copy()


def _compared_mask(df: pd.DataFrame) -> pd.Series:
    return df["valid"].astype(bool) & np.isfinite(df["official_value"].astype(float)) & \
        np.isfinite(df["diff_pct"].astype(float))


def _ols_slope(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Slope and its (naive) standard error."""
    n = len(x)
    if n < 3 or np.ptp(x) == 0:
        return math.nan, math.nan
    xm, ym = x.mean(), y.mean()
    sxx = ((x - xm) ** 2).sum()
    b = ((x - xm) * (y - ym)).sum() / sxx
    resid = y - (ym + b * (x - xm))
    s2 = (resid ** 2).sum() / (n - 2)
    return float(b), float(math.sqrt(s2 / sxx))


def _t(mean: float, se: float) -> float:
    return mean / se if se and math.isfinite(se) and se > 0 else math.nan


def per_day_stats(oos: pd.DataFrame, thresholds: dict) -> list[dict]:
    lt = _local(oos["market_ts"])
    out = []
    for d, g in oos.groupby(lt.dt.date.to_numpy()):
        glt = _local(g["market_ts"])
        m = _compared_mask(g)
        p = g.loc[m, "diff_pct"].astype(float)
        pts = g.loc[m, "diff_pts"].astype(float)
        hrs = ((glt[m] - glt.iloc[0]).dt.total_seconds() / 3600).to_numpy()
        slope, slope_se = _ols_slope(hrs, p.to_numpy() * 100) if len(p) else (math.nan, math.nan)  # bp / hour
        out.append({
            "date": d.isoformat(),
            "first_ts": glt.iloc[0].isoformat(),
            "last_ts": glt.iloc[-1].isoformat(),
            "n_rows": int(len(g)),
            "n_valid": int(g["valid"].astype(bool).sum()),
            "n_compared": int(m.sum()),
            "mean_pts": _f(pts.mean()), "mean_pct": _f(p.mean()),
            "mae_pts": _f(pts.abs().mean()), "mae_pct": _f(p.abs().mean()),
            "max_abs_pts": _f(pts.abs().max()), "max_abs_pct": _f(p.abs().max()),
            "sd_pct": _f(p.std()),
            "slope_bp_per_h": _f(slope), "slope_se": _f(slope_se),
            "n_warn": int((p.abs() > thresholds["warn_pct"]).sum()),
            "n_crit": int((p.abs() > thresholds["crit_pct"]).sum()),
        })
    return out


def _ar1_rho(oos: pd.DataFrame) -> float:
    """Lag-1 autocorrelation of diff_pct between consecutive minutes of the same day."""
    m = _compared_mask(oos)
    df = oos.loc[m, ["market_ts", "diff_pct"]].copy()
    df["ts"] = pd.to_datetime(df["market_ts"], utc=True)
    df = df.sort_values("ts")
    df["d"] = _local(df["ts"]).dt.date
    df["prev"] = df.groupby("d")["diff_pct"].shift(1)
    df["gap"] = df.groupby("d")["ts"].diff().dt.total_seconds()
    pair = df[(df["gap"] == 60) & df["prev"].notna()]
    if len(pair) < 10:
        return math.nan
    return float(np.corrcoef(pair["diff_pct"].astype(float), pair["prev"].astype(float))[0, 1])


def _invalid_breakdown(oos: pd.DataFrame) -> dict:
    inv = oos[~oos["valid"].astype(bool)]
    by_reason: dict[str, int] = {}
    by_symbol: dict[str, dict[str, int]] = {}
    for r in inv["invalid_reason"].fillna("").astype(str):
        seen = set()
        for part in filter(None, r.split("|")):
            k = reason_key(part)
            if k not in seen:
                by_reason[k] = by_reason.get(k, 0) + 1
                seen.add(k)
            if ":" in part:
                for s in part.split(":", 1)[1].split(","):
                    s = s.strip()
                    if s:
                        by_symbol.setdefault(s, {}).setdefault(k, 0)
                        by_symbol[s][k] += 1
    valid = oos["valid"].astype(bool)
    no_off = valid & ~np.isfinite(oos["official_value"].astype(float))
    # informational: how far the (excluded) unchecked value was from the official one
    unchecked_mae_pct = None
    if "calc_value_unchecked" in inv.columns and len(inv):
        u = inv["calc_value_unchecked"].astype(float)
        o = inv["official_value"].astype(float)
        ok = np.isfinite(u) & np.isfinite(o)
        if ok.any():
            unchecked_mae_pct = _f(((u[ok] - o[ok]).abs() / o[ok] * 100).mean())
    sym_rows = sorted(({"symbol": s, "total": sum(v.values()), **v} for s, v in by_symbol.items()),
                      key=lambda r: -r["total"])
    return {
        "n_invalid": int(len(inv)),
        "n_no_official": int(no_off.sum()),
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        "by_symbol": sym_rows,
        "unchecked_mae_pct": unchecked_mae_pct,
    }


def compute_metrics(results: pd.DataFrame, calibration_date: date,
                    thresholds: dict | None = None, close_check: list[dict] | None = None) -> dict:
    """All headline numbers + verdict, on out-of-sample rows only."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    oos = oos_rows(results, calibration_date)
    if oos.empty:
        raise ValueError("no out-of-sample rows after the calibration date")
    summ = {k: (_f(v) if isinstance(v, float) else v) for k, v in replication_error_summary(oos).items()}
    m = _compared_mask(oos)
    p = oos.loc[m, "diff_pct"].astype(float)
    n = int(len(p))
    days = per_day_stats(oos, th)

    # --- bias significance --------------------------------------------------
    sd = float(p.std()) if n > 1 else math.nan
    se_naive = sd / math.sqrt(n) if n > 1 else math.nan
    rho = _ar1_rho(oos)
    n_eff = n * (1 - rho) / (1 + rho) if math.isfinite(rho) and -1 < rho < 1 else math.nan
    se_ar1 = sd / math.sqrt(n_eff) if n_eff and math.isfinite(n_eff) and n_eff > 1 else math.nan
    dm = np.array([d["mean_pct"] for d in days if d["mean_pct"] is not None and d["n_compared"] >= 30])
    se_days = float(dm.std(ddof=1) / math.sqrt(len(dm))) if len(dm) > 2 else math.nan
    bias_pct = float(p.mean()) if n else math.nan
    t_days = _t(float(dm.mean()) if len(dm) else math.nan, se_days)

    # --- intraday drift: mean of per-day slopes ------------------------------
    sl = np.array([d["slope_bp_per_h"] for d in days
                   if d["slope_bp_per_h"] is not None and d["n_compared"] >= 60])
    sl_mean = float(sl.mean()) if len(sl) else math.nan
    sl_se = float(sl.std(ddof=1) / math.sqrt(len(sl))) if len(sl) > 2 else math.nan

    # --- cross-day drift: daily mean error vs trading-day index --------------
    idx = np.array([i for i, d in enumerate(days) if d["mean_pct"] is not None and d["n_compared"] >= 30], float)
    cd_slope, cd_se = _ols_slope(idx, dm * 100) if len(dm) else (math.nan, math.nan)   # bp / trading day

    # --- shares within bands --------------------------------------------------
    within_warn = float((p.abs() <= th["warn_pct"]).mean() * 100) if n else math.nan
    within_crit = float((p.abs() <= th["crit_pct"]).mean() * 100) if n else math.nan

    lag = lag_diagnostics(oos)
    corr = dict(zip(lag["lag"].astype(int), lag["corr"].astype(float)))
    best_lag = lag.attrs.get("best_lag")

    # first vs last third of the period (drift in plain words)
    k = max(1, len(dm) // 3)
    early, late = (float(dm[:k].mean()), float(dm[-k:].mean())) if len(dm) >= 3 else (math.nan, math.nan)

    stat_sig = math.isfinite(t_days) and abs(t_days) >= th["bias_t"]
    econ = math.isfinite(bias_pct) and abs(bias_pct) >= th["bias_econ_pct"]
    drift_sig = math.isfinite(cd_slope) and math.isfinite(cd_se) and abs(_t(cd_slope, cd_se)) >= th["drift_t"]
    intraday_sig = math.isfinite(sl_mean) and abs(_t(sl_mean, sl_se)) >= th["drift_t"]

    verdict = {
        "systematic_bias": bool(stat_sig and econ),
        "significant_but_small": bool(stat_sig and not econ),
        "cross_day_drift": bool(drift_sig),
        "intraday_drift": bool(intraday_sig),
        "late_bias_exceeds_econ": bool(math.isfinite(late) and abs(late) >= th["bias_econ_pct"]),
    }
    verdict.update(_verdict_text(verdict, bias_pct, summ, t_days, cd_slope, early, late, within_warn,
                                 best_lag, th))

    stal = oos.loc[oos["valid"].astype(bool), "max_staleness_s"].astype(float)
    return {
        "thresholds": th,
        "summary": summ,
        "n_minutes_oos": int(len(oos)),
        "n_compared": n,
        "within_warn_pct": _f(within_warn),
        "within_crit_pct": _f(within_crit),
        "bias": {
            "mean_pct": _f(bias_pct), "mean_pts": summ.get("bias_pts"), "sd_pct": _f(sd), "n": n,
            "se_naive": _f(se_naive), "t_naive": _f(_t(bias_pct, se_naive)),
            "ar1_rho": _f(rho), "n_eff": _f(n_eff), "se_ar1": _f(se_ar1), "t_ar1": _f(_t(bias_pct, se_ar1)),
            "n_days": int(len(dm)), "mean_of_daily_pct": _f(dm.mean() if len(dm) else math.nan),
            "se_days": _f(se_days), "t_days": _f(t_days),
            "early_mean_pct": _f(early), "late_mean_pct": _f(late), "third_len_days": int(k),
        },
        "intraday_drift": {"mean_slope_bp_per_h": _f(sl_mean), "se": _f(sl_se), "t": _f(_t(sl_mean, sl_se)),
                           "n_days": int(len(sl))},
        "cross_day_drift": {"slope_bp_per_day": _f(cd_slope), "se": _f(cd_se), "t": _f(_t(cd_slope, cd_se))},
        "lag": {"best_lag": None if best_lag is None else int(best_lag), "corr0": _f(corr.get(0)),
                "corr_best": _f(corr.get(best_lag)) if best_lag is not None else None,
                "table": [{"lag": int(k_), "corr": _f(v)} for k_, v in corr.items()]},
        "per_day": days,
        "invalid": _invalid_breakdown(oos),
        "staleness": {
            "median_max_staleness_s": summ.get("median_max_staleness_s"),
            "p95_max_staleness_s": _f(stal.quantile(0.95)) if len(stal) else None,
            "median_weighted_staleness_s": summ.get("median_weighted_staleness_s"),
            "p95_weighted_staleness_s": summ.get("p95_weighted_staleness_s"),
            "pct_rows_with_stale": _f((oos["n_stale"].astype(float) > 0).mean() * 100),
        },
        "close_check": close_check or [],
        "verdict": verdict,
    }


# ------------------------------------------------------------------- Turkish text
def tr_num(x, nd: int = 2, sign: bool = False) -> str:
    if x is None or not math.isfinite(float(x)):
        return "–"
    s = f"{abs(float(x)):,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if sign:
        return ("+" if x > 0 else "−" if x < 0 else "±") + s
    return ("−" if x < 0 else "") + s


def tr_pct(x, nd: int = 3, sign: bool = False) -> str:
    if x is None or not math.isfinite(float(x)):
        return "–"
    body = tr_num(abs(x), nd)
    if sign:
        return ("+" if x > 0 else "−" if x < 0 else "±") + "%" + body
    return ("−" if x < 0 else "") + "%" + body


def _verdict_text(v, bias_pct, summ, t_days, cd_slope, early, late, within_warn, best_lag, th) -> dict:
    mae_pct = summ.get("mean_abs_pct")
    if v["systematic_bias"]:
        answer, level = "Evet", "crit"
        head = (f"Hesapladığımız XU030 resmî endeksten sistematik olarak {tr_pct(bias_pct, 3, True)} "
                f"sapıyor.")
    elif v["cross_day_drift"] and v["late_bias_exceeds_econ"]:
        answer, level = "Evet, zamanla artan", "warn"
        head = (f"Ortalama sapma küçük ({tr_pct(bias_pct, 3, True)}), ama dönem sonuna doğru "
                f"{tr_pct(late, 3, True)} düzeyine çıkıyor.")
    elif v["significant_but_small"]:
        answer, level = "Küçük ama anlamlı", "warn"
        head = (f"Hesapladığımız XU030 resmî endekse ortalama {tr_pct(mae_pct, 3)} yakın; "
                f"{tr_pct(bias_pct, 3, True)} ortalama fark istatistiksel olarak anlamlı ama küçük.")
    else:
        answer, level = "Hayır", "good"
        head = (f"Hesapladığımız XU030 resmî endekse ortalama {tr_pct(mae_pct, 3)} yakın; "
                f"sistematik sapma bulunmadı.")
    lines = [
        f"Ortalama fark {tr_pct(bias_pct, 3, True)} ({tr_num(summ.get('bias_pts'), 2, True)} puan); "
        f"gün ortalamalarıyla t = {tr_num(t_days, 1)} (eşik |t| ≥ {tr_num(th['bias_t'], 0)}, "
        f"ekonomik eşik {tr_pct(th['bias_econ_pct'], 2)}).",
        f"Günlük ortalama farkın eğimi {tr_num(cd_slope, 2, True)} baz puan/gün; ilk üçte bir "
        f"{tr_pct(early, 3, True)}, son üçte bir {tr_pct(late, 3, True)}.",
        f"Karşılaştırılan dakikaların %{tr_num(within_warn, 1)} kadarı ±{tr_pct(th['warn_pct'], 2)} bandında.",
        "Zaman kayması yok: getiriler aynı dakikada en yüksek korelasyonu veriyor." if best_lag == 0 else
        f"En yüksek getiri korelasyonu {best_lag} dakika kaymada (yalnız teşhis).",
    ]
    return {"answer": answer, "level": level, "headline": head, "lines": lines}
