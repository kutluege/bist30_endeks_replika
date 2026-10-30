"""Synthetic tests for bist30.analysis (no network)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from bist30.analysis import (
    Feasibility,
    LatencyMismatchError,
    active_contracts,
    basis_frame,
    basis_summary,
    build_report,
    contract_from_code,
    convert_rate,
    daily_settlement_price,
    expiry_timestamp,
    final_settlement_price,
    imbalance_predictive_eval,
    implied_rate,
    last_trading_day,
    lead_lag_analysis,
    orderbook_features,
    plot_basis,
    plot_correlogram,
    plot_error_hist,
    plot_intraday,
    price_limits,
    replication_metrics,
    round_to_tick,
    standard_feasibility,
    theoretical_futures,
    year_fraction,
)
from bist30.analysis.metrics import delay_stats
from bist30.models import BAR_COLUMNS, INDEX_RESULT_COLUMNS, LatencyClass, OrderBookSnapshot

IST = "Europe/Istanbul"
DEC26 = contract_from_code("F_XU0301226")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def _grid(days=("2026-10-26", "2026-10-27"), start="10:01", end="18:00", freq="1min") -> pd.DatetimeIndex:
    parts = [pd.date_range(f"{d} {start}", f"{d} {end}", freq=freq, tz=IST) for d in days]
    return parts[0].append(parts[1:]).tz_convert("UTC") if len(parts) > 1 else parts[0].tz_convert("UTC")


def make_index_result(days=("2026-10-26", "2026-10-27"), seed=0, noise=0.5, bias=0.0,
                      latency=LatencyClass.DELAYED_15M.value) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = _grid(days)
    off = 11000 * np.exp(np.cumsum(rng.normal(0, 3e-4, len(ts))))
    calc = off + bias + rng.normal(0, noise, len(ts))
    df = pd.DataFrame({c: np.nan for c in INDEX_RESULT_COLUMNS}, index=range(len(ts)))
    df["market_ts"] = ts
    df["calc_value"] = calc
    df["official_value"] = off
    df["raw_mcap"] = calc * 1e9
    df["divisor"] = 1e9
    df["n_components"] = 30
    df["n_stale"] = 0
    df["max_staleness_s"] = 60.0
    df["missing_symbols"] = ""
    df["stale_symbols"] = ""
    df["valid"] = True
    df["invalid_reason"] = ""
    df["latency_class"] = latency
    df["price_source"] = "synthetic"
    df["official_source"] = "synthetic"
    df["diff_pts"] = df["calc_value"] - df["official_value"]
    df["diff_pct"] = df["diff_pts"] / df["official_value"] * 100
    return df


def make_futures_bars(index_result: pd.DataFrame, contract=DEC26, rate=0.40, noise=0.0, seed=1,
                      latency=LatencyClass.DELAYED_15M.value, shift_s=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.DatetimeIndex(index_result["market_ts"])
    fv = theoretical_futures(pd.Series(index_result["official_value"].to_numpy(), index=ts), contract, rate)
    px = fv.to_numpy() + rng.normal(0, noise, len(ts)) if noise else fv.to_numpy()
    mts = ts + pd.Timedelta(seconds=shift_s)
    return pd.DataFrame({
        "symbol": contract.code, "instrument_type": "future",
        "bar_start": mts - pd.Timedelta(minutes=1), "market_ts": mts,
        "open": px, "high": px, "low": px, "close": px, "volume": 100.0,
        "source": "synthetic", "latency_class": latency, "interval": "1m",
        "received_ts": mts + pd.Timedelta(minutes=15, seconds=5),
    })[BAR_COLUMNS]


# --------------------------------------------------------------------------
# contracts
# --------------------------------------------------------------------------
def test_contract_from_code_spec():
    c = contract_from_code("F_XU0301226")
    assert c.code == "F_XU0301226" and c.underlying == "XU030"
    assert c.expiry == date(2026, 12, 31)
    assert c.multiplier == 10.0 and c.tick_size == 1.0
    assert contract_from_code("F_XU0301026").expiry == date(2026, 10, 30)  # 29 Oct holiday -> Fri 30th
    for bad in ("F_XU0301126", "XU0301226", "F_XU1001226", "F_XU030M1226"):
        with pytest.raises(ValueError):
            contract_from_code(bad)


def test_last_trading_day_half_day_rule():
    # Oct 2027: 29th (Fri) holiday, 28th half day -> preceding business day 27th
    assert last_trading_day(2027, 10) == date(2027, 10, 27)
    assert last_trading_day(2026, 2) == date(2026, 2, 27)  # 28 Feb 2026 is Saturday


def test_active_contracts_on_expiry_day():
    codes = [c.code for c in active_contracts(date(2026, 10, 30))]
    assert codes == ["F_XU0301026", "F_XU0301226", "F_XU0300227"]
    codes = [c.code for c in active_contracts(date(2026, 11, 2))]
    assert codes == ["F_XU0301226", "F_XU0300227", "F_XU0300427"]
    # January: three nearest are Feb/Apr/Jun -> extra December added
    codes = [c.code for c in active_contracts(date(2027, 1, 4))]
    assert codes[-1] == "F_XU0301227" and len(codes) == 4


def test_round_and_limits():
    assert round_to_tick(10240.5) == 10241.0
    assert round_to_tick(10240.49) == 10240.0
    lo, hi = price_limits(10000.4, "normal")
    assert (lo, hi) == (9000.0, 11000.0)
    lo, hi = price_limits(10005, "evening")
    assert lo == np.ceil(10005 * 0.97) and hi == np.floor(10005 * 1.03)


def test_final_settlement_price_twap():
    exp = date(2026, 10, 30)
    t0 = pd.Timestamp("2026-10-30 17:30", tz=IST).tz_convert("UTC")
    # value 10000 for first 15 min, 10100 for last 15 min -> TWAP 10050
    idx = pd.DatetimeIndex([t0 - pd.Timedelta(minutes=1), t0 + pd.Timedelta(minutes=15)])
    s = pd.Series([10000.0, 10100.0], index=idx)
    r = final_settlement_price(s, closing_value=10200.0, expiry=exp)
    assert r["twap"] == pytest.approx(10050.0)
    assert r["fsp"] == round_to_tick(0.8 * 10050 + 0.2 * 10200)
    assert r["coverage"] == pytest.approx(1.0)
    empty = final_settlement_price(pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC")), 1.0, exp)
    assert empty["status"].startswith("not feasible") and np.isnan(empty["fsp"])


def test_daily_settlement_methods():
    end = pd.Timestamp("2026-10-29 15:10", tz="UTC")
    ts = [end - pd.Timedelta(seconds=30 * i) for i in range(12)]
    tr = pd.DataFrame({"market_ts": ts, "price": [100.0] * 6 + [110.0] * 6, "qty": [1] * 6 + [3] * 6})
    p, how = daily_settlement_price(tr, end)
    assert how == "vwap_last_10min" and p == round_to_tick((600 + 110 * 18) / 24)
    p, how = daily_settlement_price(tr.iloc[:0], end, previous_settlement=99.0)
    assert (p, how) == (99.0, "previous_settlement")


# --------------------------------------------------------------------------
# fair value
# --------------------------------------------------------------------------
def test_year_fraction_act365():
    ts = expiry_timestamp(DEC26) - pd.Timedelta(days=73)
    tau = year_fraction(pd.DatetimeIndex([ts]), DEC26)[0]
    assert tau == pytest.approx(73 / 365)
    after = year_fraction(pd.DatetimeIndex([expiry_timestamp(DEC26) + pd.Timedelta(hours=1)]), DEC26)[0]
    assert np.isnan(after)


def test_theoretical_futures_simple_continuous_dividends():
    ts = expiry_timestamp(DEC26) - pd.Timedelta(days=73)
    S = pd.Series([10000.0], index=pd.DatetimeIndex([ts]))
    tau = 73 / 365
    assert theoretical_futures(S, DEC26, 0.40).iloc[0] == pytest.approx(10000 * (1 + 0.40 * tau))
    assert theoretical_futures(S, DEC26, 0.40, compounding="continuous").iloc[0] == \
        pytest.approx(10000 * np.exp(0.40 * tau))
    assert theoretical_futures(S, DEC26, 0.40, div_yield=0.05).iloc[0] == \
        pytest.approx(10000 * (1 + 0.35 * tau))
    # explicit dividend: 20 points ex on 2026-12-01, future-valued to expiry
    fv_div = theoretical_futures(S, DEC26, 0.40, dividends=[(date(2026, 12, 1), 20.0)]).iloc[0]
    ex_ts = pd.Timestamp("2026-12-01 10:00", tz=IST)
    tau_i = (expiry_timestamp(DEC26) - ex_ts).total_seconds() / (365 * 86400)
    assert fv_div == pytest.approx(10000 * (1 + 0.40 * tau) - 20 * (1 + 0.40 * tau_i))
    # dividend after expiry or already ex -> ignored
    assert theoretical_futures(S, DEC26, 0.40, dividends=[(date(2027, 1, 5), 20.0)]).iloc[0] == \
        pytest.approx(10000 * (1 + 0.40 * tau))
    assert np.isnan(theoretical_futures(S, DEC26, None).iloc[0])
    with pytest.raises(ValueError):
        theoretical_futures(S, DEC26, 40.0)


def test_implied_rate_inverts_fair_value_and_rate_conversion():
    tau = np.array([0.2, 0.5])
    S = np.array([10000.0, 10000.0])
    for comp in ("simple", "continuous", "annual"):
        F = theoretical_futures(pd.Series(S, index=expiry_timestamp(DEC26) - pd.to_timedelta(tau * 365, "D")),
                                DEC26, 0.42, compounding=comp).to_numpy()
        r = implied_rate(F, S, tau, compounding=comp)
        assert r == pytest.approx([0.42, 0.42], rel=1e-9)
    rc = convert_rate(0.40, 0.25, "simple", "continuous")
    assert rc == pytest.approx(np.log(1 + 0.40 * 0.25) / 0.25)
    assert convert_rate(rc, 0.25, "continuous", "simple") == pytest.approx(0.40)
    assert np.isnan(implied_rate(np.array([10010.0]), np.array([10000.0]), np.array([0.5 / 365]))[0])


def test_rate_series_asof_alignment():
    ir = make_index_result(days=("2026-10-26", "2026-10-27"))
    rate = pd.Series([0.40, 0.45], index=pd.to_datetime(["2026-10-01", "2026-10-27"]))
    fb = make_futures_bars(ir, rate=0.40)
    bf = basis_frame(ir, fb, DEC26, rate)
    day = pd.DatetimeIndex(bf["market_ts"]).tz_convert(IST).date
    assert set(bf.loc[day == date(2026, 10, 26), "rate"]) == {0.40}
    assert set(bf.loc[day == date(2026, 10, 27), "rate"]) == {0.45}


# --------------------------------------------------------------------------
# basis
# --------------------------------------------------------------------------
def test_basis_exact_alignment_and_zero_residual():
    ir = make_index_result()
    fb = make_futures_bars(ir, rate=0.40)
    bf = basis_frame(ir, fb, DEC26, 0.40)
    assert bf.attrs["comparison_valid"] and bf["valid"].all()
    assert (bf["align_lag_s"] == 0).all()
    assert bf["residual_official"].abs().max() < 1e-6
    assert bf["implied_repo_official"].to_numpy() == pytest.approx(0.40, abs=1e-9)
    # computed spot residual = -(calc - official) * carry factor
    np.testing.assert_allclose(bf["residual_calc"], -(bf["spot_calc"] - bf["spot_official"]) *
                               (1 + 0.40 * bf["tau_years"]), atol=1e-6)
    s = basis_summary(bf)
    assert s["status"] == "ok" and s["n_valid"] == len(bf)


def test_basis_strict_vs_tolerance():
    ir = make_index_result(days=("2026-10-26",))
    fb = make_futures_bars(ir, shift_s=20)  # futures stamped 20 s after spot grid
    strict = basis_frame(ir, fb, DEC26, 0.40)
    assert not strict["valid"].any()
    assert strict["invalid_reason"].str.contains("no_spot_at_market_ts").all()
    loose = basis_frame(ir, fb, DEC26, 0.40, tolerance="30s")
    assert loose["valid"].all() and (loose["align_lag_s"] == 20).all()
    tight = basis_frame(ir, fb, DEC26, 0.40, tolerance="10s")
    assert not tight["valid"].any()


def test_basis_latency_mismatch_refused_or_flagged():
    ir = make_index_result(latency="delayed_15m")
    fb = make_futures_bars(ir, latency="realtime")
    with pytest.raises(LatencyMismatchError):
        basis_frame(ir, fb, DEC26, 0.40)
    flagged = basis_frame(ir, fb, DEC26, 0.40, on_latency_mismatch="flag")
    assert flagged.attrs["comparison_valid"] is False
    assert not flagged["valid"].any()
    assert flagged["residual_official"].isna().all() and flagged["spot_official"].isna().all()
    assert flagged["invalid_reason"].str.contains("latency mismatch").all()
    assert basis_summary(flagged)["status"].startswith("not feasible")
    unk = make_futures_bars(ir, latency="unknown")
    with pytest.raises(LatencyMismatchError):
        basis_frame(make_index_result(latency="unknown"), unk, DEC26, 0.40)


def test_basis_calc_invalid_and_no_rate():
    ir = make_index_result(days=("2026-10-26",))
    ir.loc[:9, "valid"] = False
    ir.loc[:9, "invalid_reason"] = "stale_components"
    fb = make_futures_bars(ir)
    bf = basis_frame(ir, fb, DEC26, 0.40)
    assert (~bf.loc[:9, "calc_valid"]).all() and bf.loc[:9, "valid"].all()
    assert bf.loc[:9, "residual_calc"].isna().all()
    nr = basis_frame(ir, fb, DEC26, None)
    assert not nr["valid"].any() and nr["invalid_reason"].str.contains("no_rate").all()
    assert nr["raw_basis_official"].notna().all()   # raw basis needs no rate
    assert nr["implied_repo_official"].notna().all()


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def test_replication_metrics_known_errors():
    ir = make_index_result(noise=0.0)
    ir["calc_value"] = ir["official_value"] + np.where(np.arange(len(ir)) % 2 == 0, 1.0, -3.0)
    ir.loc[:4, "valid"] = False
    ir.loc[:4, "calc_value"] = np.nan
    ir.loc[:2, "invalid_reason"] = "missing_price:AKBNK;stale"
    ir.loc[3:4, "invalid_reason"] = "stale"
    ir.loc[10, "official_value"] = np.nan
    m = replication_metrics(ir)
    assert m["status"] == "ok"
    v = ir[ir["valid"] & ir["official_value"].notna()]
    e = v["calc_value"] - v["official_value"]
    assert m["n_compared"] == len(v)
    assert m["mae_pts"] == pytest.approx(e.abs().mean())
    assert m["rmse_pts"] == pytest.approx(np.sqrt((e ** 2).mean()))
    assert m["max_abs_pts"] == pytest.approx(3.0)
    assert m["bias_pts"] == pytest.approx(e.mean())
    assert m["pct_valid"] == pytest.approx((len(ir) - 5) / len(ir) * 100)
    assert m["invalid_reasons"] == {"stale": 5, "missing_price": 3}
    assert m["n_valid_no_official"] == 1
    assert len(m["per_day"]) == 2 and m["per_day"]["n_total"].sum() == len(ir)
    assert m["tracking"]["n_returns"] > 0 and m["tracking"]["te_per_obs_bps"] > 0


def test_replication_metrics_refuses_mixed_latency():
    ir = make_index_result()
    ir.loc[:10, "latency_class"] = "realtime"
    assert replication_metrics(ir)["status"].startswith("not feasible")


def test_delay_stats_backfill_split():
    ir = make_index_result(days=("2026-10-26",))
    fb = make_futures_bars(ir)
    fb.loc[:9, "received_ts"] = fb.loc[:9, "market_ts"] + pd.Timedelta(days=2)
    d = delay_stats(fb)
    assert d.loc[0, "n_backfill"] == 10
    assert d.loc[0, "delay_median_s"] == pytest.approx(905.0)
    m = replication_metrics(ir, bars=fb)
    assert len(m["delay"]) == 1


# --------------------------------------------------------------------------
# lead-lag
# --------------------------------------------------------------------------
def _leadlag_frames(lead=2, n_days=3, latency_f="delayed_15m", latency_s="delayed_15m", seed=3):
    rng = np.random.default_rng(seed)
    days = [str(d.date()) for d in pd.bdate_range("2026-10-19", periods=n_days)]
    ir = make_index_result(days=tuple(days), latency=latency_s)
    ts = pd.DatetimeIndex(ir["market_ts"])
    dayk = ts.tz_convert(IST).date
    lf = np.empty(len(ts))
    ls = np.empty(len(ts))
    for d in np.unique(dayk):
        idx = np.where(dayk == d)[0]
        eff = np.cumsum(rng.normal(0, 4e-4, len(idx)))
        lf[idx] = eff + rng.normal(0, 2e-5, len(idx))
        lagged = np.r_[np.zeros(lead), eff[:-lead]] if lead else eff
        ls[idx] = lagged + rng.normal(0, 2e-5, len(idx))
    ir["official_value"] = 11000 * np.exp(ls)
    fut = make_futures_bars(ir, latency=latency_f)
    px = 11100 * np.exp(lf)
    for c in ("open", "high", "low", "close"):
        fut[c] = px
    return fut, ir


def test_lead_lag_detects_futures_lead():
    fut, ir = _leadlag_frames(lead=2)
    res = lead_lag_analysis(fut, ir, max_lag=5)
    assert res.feasible, res.reason
    s = res.result["summary"]
    assert s["best_lag"] == 2
    assert s["lead_ratio_fut_over_spot"] > 10
    pdisc = res.result["price_discovery"]
    assert pdisc["status"] == "ok"
    assert pdisc["component_share_fut"] > 0.7
    assert pdisc["info_share_fut_lower"] > 0.5


def test_lead_lag_refusals_have_no_numbers():
    fut, ir = _leadlag_frames(latency_f="realtime")
    r = lead_lag_analysis(fut, ir)
    assert not r.feasible and r.result is None and "latency" in r.reason
    assert r.status.startswith("not feasible")
    fut, ir = _leadlag_frames()
    fut5 = fut.iloc[::5].copy()
    fut5["interval"] = "5m"
    r = lead_lag_analysis(fut5, ir)
    assert not r.feasible and "coarse" in r.reason
    r = lead_lag_analysis(fut.iloc[:100], ir.iloc[:100])
    assert not r.feasible and "insufficient" in r.reason
    assert not lead_lag_analysis(None, ir).feasible
    stale = ir.copy()
    stale["official_value"] = 11000.0
    r = lead_lag_analysis(fut, stale)
    assert not r.feasible and "stale" in r.reason


# --------------------------------------------------------------------------
# order book
# --------------------------------------------------------------------------
def _snap(ts, bids, asks, lat=LatencyClass.REALTIME):
    return OrderBookSnapshot(symbol="F_XU0301226", market_ts=ts, received_ts=ts, source="synthetic",
                             latency_class=lat, bids=bids, asks=asks)


def test_orderbook_features_values():
    t = pd.Timestamp("2026-10-30 08:00", tz="UTC")
    f = orderbook_features([_snap(t, [(100, 30), (99, 10), (98, 10)], [(101, 10), (102, 10), (103, 30)])])
    r = f.iloc[0]
    assert r["mid"] == 100.5 and r["spread"] == 1.0
    assert r["imbalance_l1"] == pytest.approx((30 - 10) / 40)
    assert r["imbalance_l3"] == pytest.approx(0.0)
    assert r["microprice"] == pytest.approx((101 * 30 + 100 * 10) / 40)
    assert r["spread_bps"] == pytest.approx(1 / 100.5 * 1e4)
    assert bool(r["book_ok"])
    crossed = orderbook_features([_snap(t, [(101, 5)], [(100, 5)])]).iloc[0]
    assert not crossed["book_ok"] and np.isnan(crossed["microprice"])


def test_imbalance_eval_synthetic_signal_and_refusal():
    rng = np.random.default_rng(7)
    t0 = pd.Timestamp("2026-10-30 07:00", tz="UTC")
    mid, snaps = 10000.0, []
    for i in range(600):
        qb, qa = rng.integers(1, 50, 2)
        snaps.append(_snap(t0 + pd.Timedelta(seconds=i), [(mid - 0.5, qb)], [(mid + 0.5, qa)]))
        imb = (qb - qa) / (qb + qa)
        mid += 1.0 * np.sign(imb + rng.normal(0, 0.3))   # next move follows imbalance
    feats = orderbook_features(snaps)
    res = imbalance_predictive_eval(feats)
    assert res.feasible
    assert res.result["slope"] > 0 and res.result["slope_t"] > 5 and res.result["hit_rate_nonzero"] > 0.7
    empty = imbalance_predictive_eval(pd.DataFrame())
    assert not empty.feasible and "depth" in empty.reason


# --------------------------------------------------------------------------
# plots & report
# --------------------------------------------------------------------------
def test_plots_and_report(tmp_path):
    ir = make_index_result()
    fb = make_futures_bars(ir, noise=2.0)
    bf = basis_frame(ir, fb, DEC26, 0.40)
    fut, sp = _leadlag_frames()
    ll = lead_lag_analysis(fut, sp, max_lag=5)
    figs = {
        "Gün içi": plot_intraday(ir, tmp_path / "fig" / "intraday.png"),
        "Hata histogramı": plot_error_hist(ir, tmp_path / "fig" / "hist.png"),
        "Baz": plot_basis(bf, tmp_path / "fig" / "basis.png"),
        "Korelogram": plot_correlogram(ll.result["ccf"], tmp_path / "fig" / "ccf.png"),
    }
    for p in figs.values():
        assert p.exists() and p.stat().st_size > 5000
    flagged = basis_frame(ir, make_futures_bars(ir, latency="realtime"), DEC26, 0.4, on_latency_mismatch="flag")
    with pytest.raises(ValueError):
        plot_basis(flagged, tmp_path / "x.png")
    exps = standard_feasibility(have_spot_intraday=True, spot_latency="delayed_15m", have_futures=False,
                                futures_latency=None, have_rate=False)
    m = replication_metrics(ir, bars=fb)
    out = build_report(m, basis_summary=basis_summary(bf), experiments=exps, figures=figs,
                       contracts=active_contracts(date(2026, 10, 30)), out_path=tmp_path / "summary.md")
    txt = out.read_text(encoding="utf-8")
    assert "Replikasyon metrikleri" in txt and "MAE" in txt
    assert "yapılamaz" in txt and "VİOP gün içi fiyat kaynağı yok" in txt
    assert "F_XU0301226" in txt and "fig/intraday.png" in txt
    assert not [e for e in exps if not e.feasible and e.result is not None]
