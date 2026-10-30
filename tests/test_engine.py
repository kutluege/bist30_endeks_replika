"""Synthetic tests for bist30.engine (no network)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from bist30.engine import (IndexEngine, LatencyMixError, adjust_divisor, apply_index_events,
                           calibrate_divisor, compute_index_series, lag_diagnostics,
                           load_divisor_state, save_divisor_state, session_phase)
from bist30.models import BAR_COLUMNS, INDEX_RESULT_COLUMNS, Constituent

DAY1, DAY2 = date(2026, 10, 26), date(2026, 10, 27)   # Mon, Tue


def ist(d: date, hm: str, sec: int = 0) -> pd.Timestamp:
    return (pd.Timestamp(f"{d} {hm}", tz="Europe/Istanbul") + pd.Timedelta(seconds=sec)).tz_convert("UTC")


def bar(sym, ts, close, volume=100.0, latency="delayed_15m", itype="equity"):
    return {"symbol": sym, "instrument_type": itype, "bar_start": ts - pd.Timedelta("1min"),
            "market_ts": ts, "open": close, "high": close, "low": close, "close": close,
            "volume": volume, "source": "synthetic", "latency_class": latency, "interval": "1m",
            "received_ts": ts + pd.Timedelta("15min")}


def frame(rows):
    return pd.DataFrame(rows, columns=BAR_COLUMNS)


CONS = [
    Constituent("A", "A", "A.IS", 100, 0.5, 1.0),
    Constituent("B", "B", "B.IS", 200, 0.25, 1.0),
    Constituent("C", "C", "C.IS", 1000, 0.4, 1.0),
]
# raw mcap at P=(10,20,5): 10*100*.5 + 20*200*.25 + 5*1000*.4 = 500 + 1000 + 2000 = 3500


def day_bars(d, prices_by_minute, start="10:01", syms=("A", "B", "C"), volume=100.0):
    rows = []
    t0 = ist(d, start)
    for i, prices in enumerate(prices_by_minute):
        for s, p in zip(syms, prices):
            if p is not None:
                rows.append(bar(s, t0 + pd.Timedelta(minutes=i), p, volume))
    return rows


def official_rows(d, values, start="10:01"):
    t0 = ist(d, start)
    return [bar("XU030", t0 + pd.Timedelta(minutes=i), v, 0.0, itype="index")
            for i, v in enumerate(values) if v is not None]


def calibrated(bars=None, cons=CONS, official_value=1000.0, ref=None):
    ref = ref or ist(DAY1, "10:01")
    bars = bars if bars is not None else frame(day_bars(DAY1, [(10, 20, 5)]))
    off = frame([bar("XU030", ref, official_value, 0.0, itype="index")])
    return calibrate_divisor(bars, cons, off, ref)


# ---------------------------------------------------------------- formula
def test_hand_computed_index_exact():
    state = calibrated()
    assert state.divisor == 3.5 and state.method == "calibrated"
    assert state.reference_raw_mcap == 3500
    bars = frame(day_bars(DAY1, [(10, 20, 5), (10.5, 20, 5), (10.5, 19, 5.4)]))
    res = compute_index_series(bars, CONS, state)
    assert list(res.columns[:len(INDEX_RESULT_COLUMNS)]) == INDEX_RESULT_COLUMNS
    assert res["valid"].all()
    # 3500/3.5, (525+1000+2000)/3.5, (525+950+2160)/3.5
    assert res["calc_value"].tolist() == [1000.00, 1007.14, 1038.57]
    assert res["raw_mcap"].tolist() == pytest.approx([3500, 3525, 3635])


def test_official_divisor_method():
    bars = frame(day_bars(DAY1, [(10, 20, 5)]))
    st = calibrate_divisor(bars, CONS, None, ist(DAY1, "10:01"), official_divisor=2.123456789)
    assert st.method == "official" and st.divisor == 2.12345679


def test_official_matched_by_exact_ts_only():
    state = calibrated()
    bars = frame(day_bars(DAY1, [(10, 20, 5), (11, 20, 5)]))
    off = frame(official_rows(DAY1, [1000.0]) +
                [bar("XU030", ist(DAY1, "10:01", 30), 1014.0, 0.0, itype="index")])
    res = compute_index_series(bars, CONS, state, official=off)
    assert res.loc[0, "diff_pts"] == 0.0
    assert np.isnan(res.loc[1, "official_value"]) and np.isnan(res.loc[1, "diff_pts"])


def test_calibration_requires_exact_official_ts():
    bars = frame(day_bars(DAY1, [(10, 20, 5), (10, 20, 5)]))
    off = frame(official_rows(DAY1, [1000.0]))
    with pytest.raises(ValueError):
        calibrate_divisor(bars, CONS, off, ist(DAY1, "10:02"))


# ------------------------------------------------------------- look-ahead
def test_no_look_ahead():
    state = calibrated()
    rows = day_bars(DAY1, [(10, 20, 5), (10, 20, 5), (None, 20, 5)])
    rows.append(bar("A", ist(DAY1, "10:02", 30), 10.8))   # between grid points
    res = compute_index_series(frame(rows), CONS, state)
    v = dict(zip(res["market_ts"], res["calc_value"]))
    assert v[ist(DAY1, "10:02")] == 1000.0                # 10:02:30 bar not visible yet
    assert v[ist(DAY1, "10:03")] == round((540 + 1000 + 2000) / 3.5, 2)
    # truncating the future never changes the past
    rows2 = [r for r in rows if r["market_ts"] <= ist(DAY1, "10:02")]
    res2 = compute_index_series(frame(rows2), CONS, state)
    pd.testing.assert_series_equal(res2["calc_value"], res["calc_value"].iloc[:len(res2)],
                                   check_names=False)


def test_streaming_equals_batch():
    state = calibrated()
    prices = [(10 + i * 0.1, 20 - i * 0.05, 5 + (i % 3) * 0.01) for i in range(12)]
    rows = day_bars(DAY1, prices) + official_rows(DAY1, [1000 + i for i in range(12)])
    batch = compute_index_series(frame(rows), CONS, state)
    eng = IndexEngine(CONS, state)
    parts = []
    df = frame(rows)
    for ts, g in df.groupby("market_ts"):
        parts.append(eng.update(g))
    parts.append(eng.flush())
    stream = pd.concat([p for p in parts if len(p)], ignore_index=True)
    pd.testing.assert_frame_equal(batch.reset_index(drop=True), stream, check_dtype=False)


# --------------------------------------------------------------- validity
def test_staleness_flags_and_zero_volume_bars():
    state = calibrated()
    rows = day_bars(DAY1, [(10, 20, 5)] * 10, syms=("A", "B"))
    # C trades once at 10:01 then only flat zero-volume bars (Yahoo style)
    rows += [bar("C", ist(DAY1, "10:01"), 5.0)]
    rows += [bar("C", ist(DAY1, "10:01") + pd.Timedelta(minutes=i), 5.0, 0.0) for i in range(1, 10)]
    res = compute_index_series(frame(rows), CONS, state, staleness_s=300).set_index("market_ts")
    r5 = res.loc[ist(DAY1, "10:06")]          # 300 s -> not yet stale
    assert r5["valid"] and r5["n_stale"] == 0 and r5["max_staleness_s"] == 300
    r7 = res.loc[ist(DAY1, "10:07")]          # 360 s -> stale
    assert not r7["valid"] and r7["stale_symbols"] == "C" and "stale>300s:C" in r7["invalid_reason"]
    assert np.isnan(r7["calc_value"]) and r7["calc_value_unchecked"] == 1000.0
    assert r7["stale_weight"] == pytest.approx(2000 / 3500)
    assert r7["weighted_staleness_s"] == pytest.approx(2000 / 3500 * 360)
    # tolerance on stale weight keeps it valid
    res2 = compute_index_series(frame(rows), CONS, state, stale_weight_tolerance=0.6).set_index("market_ts")
    assert res2.loc[ist(DAY1, "10:07"), "valid"]


def test_missing_constituent_invalid():
    state = calibrated()
    rows = day_bars(DAY1, [(10, 20, None), (10, 20, None)])
    rows = [r for r in rows if r["symbol"] != "C"]
    res = compute_index_series(frame(rows), CONS, state)
    assert (~res["valid"]).all() and (res["missing_symbols"] == "C").all()
    assert res["invalid_reason"].str.contains("missing_price:C").all()


def test_latency_mix_is_hard_error():
    state = calibrated()
    rows = day_bars(DAY1, [(10, 20, 5)])
    rows[0]["latency_class"] = "realtime"
    with pytest.raises(LatencyMixError):
        compute_index_series(frame(rows), CONS, state)
    rows = day_bars(DAY1, [(10, 20, 5)])
    off = frame(official_rows(DAY1, [1000.0]))
    off["latency_class"] = "eod"
    with pytest.raises(LatencyMixError):
        compute_index_series(frame(rows), CONS, state, official=off)


def test_price_jump_and_session_flags():
    state = calibrated()
    rows = day_bars(DAY1, [(10, 20, 5), (12, 20, 5)])              # +20 % jump
    res = compute_index_series(frame(rows), CONS, state)
    assert not res.loc[1, "valid"] and "price_jump" in res.loc[1, "invalid_reason"]
    # outside session -> invalid; auction -> flagged but valid
    rows = day_bars(DAY1, [(10, 20, 5)], start="09:30") + day_bars(DAY1, [(10, 20, 5)], start="09:56") \
        + day_bars(DAY1, [(10, 20, 5)], start="18:03")
    res = compute_index_series(frame(rows), CONS, state).set_index("market_ts")
    assert res.loc[ist(DAY1, "09:30"), "invalid_reason"] == "outside_session"
    assert res.loc[ist(DAY1, "09:56"), "valid"] and "opening_auction" in res.loc[ist(DAY1, "09:56"), "flags"]
    assert res.loc[ist(DAY1, "18:03"), "session_phase"] == "closing_auction"
    assert session_phase(ist(date(2026, 10, 28), "12:31")) == "closing_auction"   # half day
    assert session_phase(ist(date(2026, 10, 31), "11:00")) == "closed"            # Saturday


# ---------------------------------------------------------------- divisor
def test_adjust_divisor_rule_5_1():
    st = calibrated()
    st2 = adjust_divisor(st, delta_mcap=350.0, mcap_t=3500.0, reason="test", effective_date=DAY2)
    assert st2.divisor == round(3.5 * 1.1, 8) and st.divisor == 3.5
    assert st2.adjustments[-1]["index_before"] == pytest.approx(st2.adjustments[-1]["index_after"])


def _two_day(cons, d2_prices, actions=None, d1_prices=((10, 20, 5),), syms=("A", "B", "C"),
             syms2=None):
    state = calibrated()
    rows = day_bars(DAY1, list(d1_prices) * 3, syms=syms)
    rows += day_bars(DAY2, d2_prices, syms=syms2 or syms)
    return compute_index_series(frame(rows), cons, state, actions=actions)


def test_continuity_constituent_swap():
    # C leaves after DAY1, D (price 8, N=500, H=0.5) enters on DAY2
    cons = [CONS[0], CONS[1],
            Constituent("C", "C", "C.IS", 1000, 0.4, 1.0, effective_to=DAY1),
            Constituent("D", "D", "D.IS", 500, 0.5, 1.0, effective_from=DAY2)]
    res = _two_day(cons, [(10, 20, 8)], d1_prices=((10, 20, 5, 8),), syms=("A", "B", "C", "D"),
                   syms2=("A", "B", "D"))
    d1_last, d2_first = res.iloc[2], res.iloc[3]
    assert d1_last["calc_value"] == 1000.0 and d2_first["calc_value"] == 1000.0   # continuity
    new_div = 3.5 * (500 + 1000 + 2000) / 3500   # PD_new = 500+1000+8*500*.5=3500 -> unchanged here
    assert d2_first["divisor"] == pytest.approx(new_div)
    st = res.attrs["divisor_state"]
    assert st.adjustments[-1]["type"] == "adjustment"


def test_continuity_swap_changes_divisor():
    cons = [CONS[0], CONS[1],
            Constituent("C", "C", "C.IS", 1000, 0.4, 1.0, effective_to=DAY1),
            Constituent("D", "D", "D.IS", 1000, 0.5, 1.0, effective_from=DAY2)]
    res = _two_day(cons, [(10, 20, 8)], d1_prices=((10, 20, 5, 8),), syms=("A", "B", "C", "D"),
                   syms2=("A", "B", "D"))
    # PD_t = 3500, PD_new = 500+1000+8*1000*.5 = 5500 -> B = 3.5 * 5500/3500 = 5.5
    assert res.iloc[3]["divisor"] == 5.5
    assert res.iloc[3]["calc_value"] == res.iloc[2]["calc_value"] == 1000.0


def test_bonus_issue_divisor_unchanged_and_index_continuous():
    acts = pd.DataFrame([{"symbol": "A", "action_type": "bonus_issue", "effective_date": DAY2,
                          "ratio": 1.0, "affects_price_index": True}])
    cons = list(CONS)
    # A halves to 5 on DAY2 (100 % bonus) -> index unchanged
    res = _two_day(cons, [(5, 20, 5)], actions=acts)
    assert res.iloc[3]["divisor"] == 3.5
    assert res.iloc[3]["calc_value"] == 1000.0
    assert res.iloc[3]["valid"]                       # price limit uses adjusted base
    st = res.attrs["divisor_state"]
    assert st.adjustments[-1]["delta_mcap"] == 0.0


def test_bonus_issue_before_first_trade_uses_adjusted_price():
    acts = pd.DataFrame([{"symbol": "A", "action_type": "bonus_issue", "effective_date": DAY2,
                          "ratio": 1.0}])
    res = _two_day(list(CONS), [(None, 20, 5)], actions=acts)   # A not traded yet on DAY2
    assert res.iloc[3]["calc_value_unchecked"] == 1000.0


def test_ff_change_divisor_continuity():
    acts = pd.DataFrame([{"symbol": "C", "action_type": "ff_change", "effective_date": DAY2,
                          "new_free_float_ratio": 0.5}])
    res = _two_day(list(CONS), [(10, 20, 5)], actions=acts)
    # dPD = 5*1000*(0.5-0.4) = 500 -> B = 3.5 * (1 + 500/3500) = 4.0
    assert res.iloc[3]["divisor"] == 4.0
    assert res.iloc[3]["calc_value"] == 1000.0


def test_rights_issue_and_cash_dividend():
    st = calibrated()
    closes = {"A": 10.0, "B": 20.0, "C": 5.0}
    acts = pd.DataFrame([{"symbol": "A", "action_type": "rights_issue", "effective_date": DAY2,
                          "ratio": 0.5, "cash_amount": 4.0},
                         {"symbol": "B", "action_type": "cash_dividend", "effective_date": DAY2,
                          "cash_amount": 1.0, "affects_price_index": False}])
    st2, new, prices, notes = apply_index_events(st, CONS, closes, DAY2, actions=acts)
    # A: N 100->150, P' = (10 + .5*4)/1.5 = 8 ; dPD = 150*8*.5 - 500 = 100 (= 50 new sh * 4 TL * H)
    assert prices["A"] == pytest.approx(8.0) and prices["B"] == 20.0
    assert st2.divisor == round(3.5 * (1 + 100 / 3500), 8)
    assert any("cash dividend" in n for n in notes)


def test_divisor_state_roundtrip(tmp_path):
    st = adjust_divisor(calibrated(), 10.0, 3500.0, "x", DAY2)
    p = save_divisor_state(st, tmp_path / "divisor.json")
    st2 = load_divisor_state(p)
    assert st2.divisor == st.divisor and st2.reference_market_ts == st.reference_market_ts
    assert st2.adjustments[-1]["reason"] == "x"


# ------------------------------------------------- official daily closes
def daily_bar(sym, d, close, volume=1e6, latency="eod"):
    start = pd.Timestamp(str(d), tz="Europe/Istanbul").tz_convert("UTC")
    r = bar(sym, start + pd.Timedelta("1D"), close, volume, latency=latency)
    r.update(bar_start=start, interval="1d")
    return r


def test_limit_base_uses_official_close_not_last_1m():
    # ASTOR case: last 1m (17:59) = 10.25, official close (closing auction) = 10.00,
    # next day limit-down 9.00 = -10 % vs 10.00 but -12.2 % vs 10.25
    state = calibrated()
    rows = day_bars(DAY1, [(10, 20, 5), (10.1, 20, 5), (10.25, 20, 5)])
    rows += day_bars(DAY2, [(9.0, 20, 5), (9.0, 20, 5)])
    res = compute_index_series(frame(rows), CONS, state)
    assert not res.iloc[3]["valid"] and "outside_price_limit:A" in res.iloc[3]["invalid_reason"]
    daily = frame([daily_bar("A", DAY1, 10.0), daily_bar("B", DAY1, 20.0),
                   daily_bar("C", DAY1, 5.0, volume=0.0)])   # volume 0 (Yahoo SASA) still valid
    res2 = compute_index_series(frame(rows), CONS, state, daily_closes=daily)  # eod != delayed: no error
    assert res2.iloc[3]["valid"] and res2.iloc[4]["valid"]
    bs = res2.attrs["base_sources"][DAY2.isoformat()]
    assert bs["reference_date"] == DAY1.isoformat() and bs["official_close"] == ["A", "B", "C"]
    assert bs["fallback_last_intraday"] == []


def test_divisor_adjustment_uses_official_close():
    acts = pd.DataFrame([{"symbol": "C", "action_type": "ff_change", "effective_date": DAY2,
                          "new_free_float_ratio": 0.5}])
    state = calibrated()
    rows = day_bars(DAY1, [(10, 20, 5), (10, 20, 5.2)])           # last 1m C = 5.2
    rows += day_bars(DAY2, [(10, 20, None)])                       # C not traded yet
    wide = pd.DataFrame({"A": [10.0], "B": [20.0], "C": [5.0]}, index=[pd.Timestamp(DAY1)])
    res = compute_index_series(frame(rows), CONS, state, actions=acts, daily_closes=wide)
    # PD_t = 3500 (official closes), dPD = 5*1000*0.1 = 500 -> B = 3.5*(1+500/3500) = 4.0
    assert res.iloc[2]["divisor"] == 4.0
    # before C's first trade its price is the official close -> index = 4000/4 = 1000
    assert res.iloc[2]["calc_value_unchecked"] == 1000.0
    res_fb = compute_index_series(frame(rows), CONS, state, actions=acts)   # fallback: 1m price
    assert res_fb.iloc[2]["divisor"] == round(3.5 * (1 + 520 / 3580), 8)


# ------------------------------------------------------------ diagnostics
def test_lag_diagnostic_detects_shift():
    rng = np.random.default_rng(0)
    n = 200
    calc = 1000 * np.exp(np.cumsum(rng.normal(0, 1e-3, n)))
    ts = pd.date_range(ist(DAY1, "10:01"), periods=n, freq="1min")
    off = np.r_[np.full(2, calc[0]), calc[:-2]]          # official lags by 2 minutes
    res = pd.DataFrame({"market_ts": ts, "calc_value": calc, "official_value": off, "valid": True})
    out = lag_diagnostics(res)
    assert out.attrs["best_lag"] == 2
