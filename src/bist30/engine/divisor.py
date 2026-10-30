"""Divisor (bolen) calibration, rule-5.1 adjustment, corporate actions, persistence.

Formula (kural seti md. 4, TL price index, D_t = 1):
    E_t = sum_i P_i * N_i * H_i * K_i / B_t
Rule 5.1:
    B_{t+1} = (1 + dPD / PD_t) * B_t
Precision (md. 2.19): index 2 decimals, divisor 8 decimals.

Policy: the divisor is calibrated ONCE (``calibrate_divisor``) and afterwards
only changed through ``adjust_divisor`` for rule-5.2 events. It is never
re-fitted to the official series.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from bist30.models import Constituent, DivisorState

from .session import SessionConfig, trading_date

DIVISOR_DECIMALS = 8
INDEX_DECIMALS = 2

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "state" / "divisor.json"


class CalibrationError(ValueError):
    pass


class DivisorAdjustmentError(ValueError):
    pass


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def round_divisor(x: float) -> float:
    return round(float(x), DIVISOR_DECIMALS)


def round_index(x: float) -> float:
    return round(float(x), INDEX_DECIMALS)


def constituents_fingerprint(constituents: Iterable[Constituent]) -> str:
    items = sorted((c.symbol, float(c.shares_outstanding), float(c.free_float_ratio),
                    float(c.weight_factor)) for c in constituents)
    return "sha1:" + hashlib.sha1(repr(items).encode()).hexdigest()[:16]


def active_constituents(constituents: Iterable[Constituent], on: date | None) -> list[Constituent]:
    """Constituents whose [effective_from, effective_to] covers ``on`` (None -> all)."""
    out = []
    for c in constituents:
        if on is not None:
            if c.effective_from is not None and on < c.effective_from:
                continue
            if c.effective_to is not None and on > c.effective_to:
                continue
        out.append(c)
    syms = [c.symbol for c in out]
    if len(set(syms)) != len(syms):
        dup = sorted({s for s in syms if syms.count(s) > 1})
        raise ValueError(f"overlapping constituent periods for {dup} on {on}")
    return out


def mcap_term(c: Constituent, price: float) -> float:
    """FDP weighted market cap of one constituent: P * N * H * K (D = 1, TL)."""
    return float(price) * float(c.shares_outstanding) * float(c.free_float_ratio) * float(c.weight_factor)


def raw_mcap(constituents: Iterable[Constituent], prices: Mapping[str, float]) -> float:
    total = 0.0
    missing = []
    for c in constituents:
        p = prices.get(c.symbol)
        if p is None or not math.isfinite(p) or p <= 0:
            missing.append(c.symbol)
            continue
        total += mcap_term(c, p)
    if missing:
        raise KeyError(f"missing prices for {missing}")
    return total


def _utc(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        raise ValueError(f"naive timestamp not allowed: {ts}")
    return ts.tz_convert("UTC")


def _empty_official() -> pd.Series:
    return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))


def _official_series(official, index_symbol: str = "XU030") -> tuple[pd.Series, str, str]:
    """Normalise an official-index input to (Series[value] by UTC market_ts, source, latency)."""
    if official is None:
        return _empty_official(), "", ""
    if isinstance(official, pd.Series):
        if official.empty:
            return _empty_official(), "", ""
        s = official.astype(float).copy()
        s.index = pd.DatetimeIndex(s.index).tz_convert("UTC")
        return s[~s.index.duplicated(keep="last")].sort_index(), "", ""
    df = official
    if "symbol" in df.columns:
        df = df[df["symbol"] == index_symbol]
    col = "close" if "close" in df.columns else "value"
    df = df.dropna(subset=[col])
    if "received_ts" in df.columns:
        df = df.sort_values(["market_ts", "received_ts"])
    s = pd.Series(df[col].astype(float).to_numpy(),
                  index=pd.DatetimeIndex(pd.to_datetime(df["market_ts"], utc=True)))
    s = s[~s.index.duplicated(keep="last")].sort_index()
    src = ";".join(sorted(df["source"].dropna().astype(str).unique())) if "source" in df.columns else ""
    lat = ";".join(sorted(df["latency_class"].dropna().astype(str).unique())) if "latency_class" in df.columns else ""
    return s, src, lat


def asof_prices(bars: pd.DataFrame, symbols: Iterable[str], ts) -> dict[str, dict]:
    """Last valid trade price with market_ts <= ts for each symbol (no look-ahead).

    A bar counts as a trade if volume > 0 or volume is NaN (unknown). Returns
    {symbol: {"price", "market_ts", "staleness_s"}} for symbols that have one.
    """
    ts = _utc(ts)
    symbols = list(symbols)
    df = bars[bars["symbol"].isin(symbols)].copy()
    df["market_ts"] = pd.to_datetime(df["market_ts"], utc=True)
    vol = pd.to_numeric(df["volume"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    ok = (df["market_ts"] <= ts) & close.gt(0) & (vol.isna() | vol.gt(0))
    df = df[ok].assign(close=close[ok]).sort_values("market_ts")
    out = {}
    for sym, g in df.groupby("symbol"):
        last = g.iloc[-1]
        out[sym] = {"price": float(last["close"]), "market_ts": last["market_ts"],
                    "staleness_s": (ts - last["market_ts"]).total_seconds()}
    return out


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------
def calibrate_divisor(bars: pd.DataFrame, constituents: list[Constituent], official,
                      reference_ts=None, *, official_divisor: float | None = None,
                      official_source: str | None = None, constituents_version: str | None = None,
                      index_symbol: str = "XU030", max_staleness_s: float | None = None,
                      session: SessionConfig | None = None) -> DivisorState:
    """Calibrate the divisor ONCE:  B = sum(P*N*H*K)(ref) / E_official(ref).

    * ``reference_ts``: exact market_ts at which the official value exists
      (recommended: previous day's official close, using daily closing bars
      of the same latency class). ``None`` -> latest official timestamp.
    * Prices are as-of ``reference_ts`` (never later). The official value must
      exist at exactly ``reference_ts`` (no nearest-time matching).
    * If ``official_divisor`` is given it is used as-is (method="official").
    """
    session = session or SessionConfig()
    off, off_src, off_lat = _official_series(official, index_symbol)
    if reference_ts is None:
        if off.empty:
            raise CalibrationError("no official series and no reference_ts")
        reference_ts = off.index[-1]
    ref = _utc(reference_ts)
    ref_date = trading_date(ref, session)
    act = active_constituents(constituents, ref_date)
    if not act:
        raise CalibrationError(f"no active constituents on {ref_date}")

    price_lat = set(bars.loc[bars["symbol"].isin([c.symbol for c in act]), "latency_class"]
                    .dropna().astype(str)) if "latency_class" in bars.columns else set()
    if len(price_lat) > 1:
        from .core import LatencyMixError
        raise LatencyMixError(f"calibration bars mix latency classes {sorted(price_lat)}")
    if off_lat and price_lat and {off_lat} != price_lat:
        from .core import LatencyMixError
        raise LatencyMixError(f"official latency {off_lat} != price latency {sorted(price_lat)}")

    snap = asof_prices(bars, [c.symbol for c in act], ref)
    missing = [c.symbol for c in act if c.symbol not in snap]
    if missing:
        raise CalibrationError(f"no price as of {ref} for {missing}")
    stale = {s: v["staleness_s"] for s, v in snap.items()
             if max_staleness_s is not None and v["staleness_s"] > max_staleness_s}
    if stale:
        raise CalibrationError(f"stale prices at reference {ref}: {stale}")
    prices = {s: v["price"] for s, v in snap.items()}
    mcap = raw_mcap(act, prices)

    off_val = float(off.get(ref, float("nan"))) if not off.empty else float("nan")
    if official_divisor is not None:
        method, divisor = "official", round_divisor(official_divisor)
    else:
        if not math.isfinite(off_val):
            raise CalibrationError(f"official value missing at exactly {ref} (no nearest-time matching)")
        method, divisor = "calibrated", round_divisor(mcap / off_val)

    entry = {
        "type": "calibration", "method": method, "reference_market_ts": ref.isoformat(),
        "official_value": off_val, "raw_mcap": mcap, "divisor": divisor,
        "n_constituents": len(act),
        "max_price_staleness_s": max(v["staleness_s"] for v in snap.values()),
        "prices": prices,
    }
    return DivisorState(
        divisor=divisor, reference_market_ts=ref.to_pydatetime(),
        reference_official_value=off_val, reference_raw_mcap=mcap,
        official_source=official_source or off_src, method=method,
        constituents_version=constituents_version or constituents_fingerprint(act),
        adjustments=[entry],
    )


# --------------------------------------------------------------------------
# rule 5.1
# --------------------------------------------------------------------------
def adjust_divisor(state: DivisorState, delta_mcap: float, mcap_t: float, reason: str,
                   effective_date: date, **details) -> DivisorState:
    """B_{t+1} = (1 + dPD/PD_t) * B_t, rounded to 8 decimals; appends an audit entry."""
    if not (math.isfinite(mcap_t) and mcap_t > 0):
        raise DivisorAdjustmentError(f"invalid PD_t={mcap_t}")
    if not math.isfinite(delta_mcap):
        raise DivisorAdjustmentError(f"invalid dPD={delta_mcap}")
    new_div = round_divisor(state.divisor * (1.0 + delta_mcap / mcap_t))
    if new_div <= 0:
        raise DivisorAdjustmentError(f"non-positive divisor {new_div}")
    entry = {
        "type": "adjustment", "effective_date": _iso_date(effective_date), "reason": reason,
        "old_divisor": state.divisor, "new_divisor": new_div,
        "delta_mcap": float(delta_mcap), "mcap_t": float(mcap_t),
        "index_before": mcap_t / state.divisor, "index_after": (mcap_t + delta_mcap) / new_div,
        "applied_utc": datetime.now(timezone.utc).isoformat(),
    }
    entry.update({k: _jsonable(v) for k, v in details.items()})
    return dataclasses.replace(state, divisor=new_div, adjustments=list(state.adjustments) + [entry])


def last_adjusted_date(state: DivisorState, session: SessionConfig | None = None) -> date:
    """Latest date whose events are already reflected in ``state``."""
    d = trading_date(state.reference_market_ts, session)
    for a in state.adjustments:
        if a.get("type") == "adjustment" and a.get("effective_date"):
            d = max(d, date.fromisoformat(a["effective_date"]))
    return d


# --------------------------------------------------------------------------
# corporate actions / constituent changes
# --------------------------------------------------------------------------
def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _apply_one_action(working: dict[str, Constituent], prices: dict[str, float],
                      row: Mapping) -> str:
    """Mutates working/prices for one action row; returns a note."""
    sym = str(row["symbol"])
    typ = str(row["action_type"]).strip().lower()
    c = working.get(sym)
    ratio, cash = _num(row.get("ratio")), _num(row.get("cash_amount"))
    new_n, new_h = _num(row.get("new_shares_outstanding")), _num(row.get("new_free_float_ratio"))

    if typ == "inclusion":
        if new_n is None or new_h is None:
            raise DivisorAdjustmentError(f"inclusion of {sym} needs new_shares_outstanding/new_free_float_ratio")
        working[sym] = Constituent(sym, sym, f"{sym}.IS", new_n, new_h, 1.0)
        return f"{sym} included"
    if c is None:
        if typ in ("exclusion",):
            return f"{sym} not in index (exclusion ignored)"
        return f"{sym} not in index ({typ} ignored)"
    if typ == "exclusion":
        del working[sym]
        return f"{sym} excluded"
    if typ == "cash_dividend":
        # Price index: no divisor adjustment (rule 5.2.a, only return indices).
        return f"{sym} cash dividend: no price-index adjustment"
    if typ in ("bonus_issue", "split"):
        old_n = c.shares_outstanding
        if new_n is None:
            if ratio is None:
                raise DivisorAdjustmentError(f"{typ} {sym} needs ratio or new_shares_outstanding")
            # bonus: ratio = new shares per old share; split: ratio = shares after per share before
            new_n = old_n * (1.0 + ratio) if typ == "bonus_issue" else old_n * ratio
        factor = old_n / new_n
        working[sym] = dataclasses.replace(c, shares_outstanding=new_n)
        if sym in prices:
            prices[sym] = prices[sym] * factor
        return f"{sym} {typ}: N {old_n:g}->{new_n:g}, price x{factor:.10g}"
    if typ == "rights_issue":
        if ratio is None or cash is None:
            raise DivisorAdjustmentError(f"rights_issue {sym} needs ratio and cash_amount")
        old_n = c.shares_outstanding
        new_n = new_n if new_n is not None else old_n * (1.0 + ratio)
        r = new_n / old_n - 1.0
        working[sym] = dataclasses.replace(c, shares_outstanding=new_n)
        if sym in prices:
            prices[sym] = (prices[sym] + r * cash) / (1.0 + r)   # theoretical ex-rights price
        return f"{sym} rights issue r={r:g} @ {cash:g}: N {old_n:g}->{new_n:g}"
    if typ == "ff_change":
        if new_h is None:
            raise DivisorAdjustmentError(f"ff_change {sym} needs new_free_float_ratio")
        working[sym] = dataclasses.replace(c, free_float_ratio=new_h)
        return f"{sym} FDPO {c.free_float_ratio:g}->{new_h:g}"
    if typ in ("share_conversion", "merger", "spinoff"):
        if new_n is None and new_h is None:
            raise DivisorAdjustmentError(f"{typ} {sym}: needs new_shares_outstanding/new_free_float_ratio (manual)")
        working[sym] = dataclasses.replace(
            c, shares_outstanding=new_n if new_n is not None else c.shares_outstanding,
            free_float_ratio=new_h if new_h is not None else c.free_float_ratio)
        return f"{sym} {typ}: N/H updated at closing price (no price adjustment)"
    raise DivisorAdjustmentError(f"unknown action_type {typ!r} for {sym}")


def apply_index_events(state: DivisorState, old: list[Constituent], close_prices: Mapping[str, float],
                       effective_date: date, actions: pd.DataFrame | None = None,
                       new_constituents: list[Constituent] | None = None,
                       reason: str = "") -> tuple[DivisorState, list[Constituent], dict[str, float], list[str]]:
    """Apply all rule-5.2 events effective on ``effective_date`` in one rule-5.1 step.

    ``old``: constituents used on day t; ``close_prices``: day-t closing prices.
    Order: corporate actions (``actions`` rows, CORPORATE_ACTION_COLUMNS) adjust N/H
    and the reference prices; then ``new_constituents`` (if given) replaces the
    definition of every symbol it lists / removes symbols it does not list
    (periodic constituent change, FDPO change from the constituents file).

    PD_t = sum_old P*N*H*K, PD_new = sum_new P'*N'*H'*K' (P' = adjusted price),
    dPD = PD_new - PD_t, B_{t+1} = (1 + dPD/PD_t) * B_t.
    Bonus/split: P'N' = PN -> dPD = 0 -> divisor unchanged.
    Returns (new_state, new_constituents, adjusted_prices, notes).
    """
    prices = {k: float(v) for k, v in close_prices.items() if v is not None and math.isfinite(v)}
    working = {c.symbol: c for c in old}
    notes: list[str] = []
    if actions is not None and len(actions):
        for _, row in actions.iterrows():
            notes.append(_apply_one_action(working, prices, row))
    if new_constituents is not None:
        target = {c.symbol: c for c in new_constituents}
        for s in list(working):
            if s not in target:
                notes.append(f"{s} removed (constituent list)")
                del working[s]
        for s, c in target.items():
            w = working.get(s)
            if w is None:
                notes.append(f"{s} added (constituent list)")
                working[s] = c
            elif (w.shares_outstanding, w.free_float_ratio, w.weight_factor) != (
                    c.shares_outstanding, c.free_float_ratio, c.weight_factor):
                notes.append(f"{s} N/H/K changed (constituent list)")
                working[s] = c
    new_list = list(working.values())
    pd_old_missing = [c.symbol for c in old if c.symbol not in close_prices or
                      not _num(close_prices.get(c.symbol))]
    pd_new_missing = [c.symbol for c in new_list if c.symbol not in prices]
    if pd_old_missing or pd_new_missing:
        raise DivisorAdjustmentError(
            f"missing closing prices for divisor adjustment on {effective_date}: "
            f"{sorted(set(pd_old_missing + pd_new_missing))}")
    pd_t = raw_mcap(old, close_prices)
    pd_new = raw_mcap(new_list, prices)
    if not notes:
        return state, new_list, prices, notes
    new_state = adjust_divisor(state, pd_new - pd_t, pd_t, reason or "; ".join(notes),
                               effective_date, notes=notes, pd_new=pd_new)
    return new_state, new_list, prices, notes


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------
def _iso_date(d) -> str:
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(pd.Timestamp(d).date())


def _jsonable(v):
    if isinstance(v, (pd.Timestamp, datetime)):
        return pd.Timestamp(v).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def save_divisor_state(state: DivisorState, path: str | os.PathLike | None = None) -> Path:
    path = Path(path) if path else DEFAULT_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    d = dataclasses.asdict(state)
    d = _jsonable(d)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_divisor_state(path: str | os.PathLike | None = None) -> DivisorState:
    path = Path(path) if path else DEFAULT_STATE_PATH
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    d["reference_market_ts"] = pd.Timestamp(d["reference_market_ts"]).tz_convert("UTC").to_pydatetime()
    if d.get("reference_official_value") is None:
        d["reference_official_value"] = float("nan")
    return DivisorState(**d)
