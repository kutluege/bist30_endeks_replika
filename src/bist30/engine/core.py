"""As-of synchronised index calculation (batch + streaming share one code path).

For each grid point t (bar end, default 1 minute) every constituent's last
valid trade price with market_ts <= t is used (never look-ahead). Validity
rules and their reasons are recorded per row. See docs/engine.md.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from bist30.models import INDEX_RESULT_COLUMNS, Constituent, DivisorState

from .divisor import (DivisorAdjustmentError, _empty_official, _official_series, active_constituents,
                      apply_index_events, constituents_fingerprint, last_adjusted_date,
                      mcap_term, round_index)
from .session import AUCTION_PHASES, CLOSED, CONTINUOUS, SessionConfig, session_phase, trading_date

EXTRA_RESULT_COLUMNS = [
    "session_phase",          # closed | opening_auction | continuous | closing_auction | closing_trades
    "flags",                  # soft flags (";"-joined), do not invalidate
    "calc_value_unchecked",   # raw_mcap / divisor even if invalid (debug only, 2 decimals)
    "weighted_staleness_s",   # sum_i w_i * staleness_i (w = mcap weight)
    "stale_weight",           # mcap weight of stale constituents
    "n_new",                  # constituents with a new bar in (t - grid, t]
]
RESULT_COLUMNS = INDEX_RESULT_COLUMNS + EXTRA_RESULT_COLUMNS


class LatencyMixError(ValueError):
    """Records with different latency classes must never be mixed."""


@dataclass
class _SymState:
    price: float | None = None          # last valid price (trade, or auction print)
    trade_ts: pd.Timestamp | None = None  # market_ts of last bar with an actual trade
    obs_ts: pd.Timestamp | None = None    # market_ts of last bar of any kind
    base: float | None = None           # previous session close (price-limit reference)


def _norm_bars(bars: pd.DataFrame) -> pd.DataFrame:
    need = {"symbol", "market_ts", "close", "volume"}
    miss = need - set(bars.columns)
    if miss:
        raise ValueError(f"bars missing columns {sorted(miss)}")
    df = bars.copy()
    df["market_ts"] = pd.to_datetime(df["market_ts"], utc=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    for c in ("source", "latency_class"):
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].astype(object).where(df[c].notna(), "").astype(str)
    df = df[df["close"].gt(0) & np.isfinite(df["close"])]
    if "received_ts" in df.columns:
        df = df.sort_values(["market_ts", "received_ts"], kind="stable")
    else:
        df = df.sort_values("market_ts", kind="stable")
    return df.drop_duplicates(["symbol", "market_ts"], keep="last")[
        ["symbol", "market_ts", "close", "volume", "source", "latency_class"]]


class IndexEngine:
    """Incremental XU030 calculator.

    ``update(new_bars)`` ingests bars (BAR_COLUMNS; rows of ``index_symbol`` are
    taken as the official series) and returns the newly finalised result rows.
    A grid point is finalised once a bar with a later grid point has arrived
    (or on ``flush()``), so that all bars with market_ts <= t are in.
    Finalised rows are never revised; late bars only update symbol state.
    """

    def __init__(self, constituents: list[Constituent], divisor_state: DivisorState, *,
                 grid: str = "1min", staleness_s: float = 300.0, stale_weight_tolerance: float = 0.0,
                 jump_pct: float = 0.10, limit_pct: float = 0.10, limit_tolerance: float = 0.005,
                 session: SessionConfig | None = None, actions: pd.DataFrame | None = None,
                 only_new: bool = True, auction_policy: str = "flag",
                 zero_volume_price_updates: bool = True, index_symbol: str = "XU030",
                 official=None, daily_closes=None):
        if auction_policy not in ("flag", "invalid"):
            raise ValueError("auction_policy must be 'flag' or 'invalid'")
        self.grid = pd.Timedelta(pd.tseries.frequencies.to_offset(grid))
        self._grid_str = grid
        self.staleness_s = float(staleness_s)
        self.stale_weight_tolerance = float(stale_weight_tolerance)
        self.jump_pct, self.limit_pct, self.limit_tol = jump_pct, limit_pct, limit_tolerance
        self.session = session or SessionConfig()
        self.only_new = only_new
        self.auction_policy = auction_policy
        self.zero_volume_price_updates = zero_volume_price_updates
        self.index_symbol = index_symbol
        self.divisor_state = divisor_state
        self._all = list(constituents)
        self._universe = {c.symbol for c in self._all}
        self._actions = None if actions is None or len(actions) == 0 else actions.copy()
        if self._actions is not None:
            self._actions["effective_date"] = pd.to_datetime(self._actions["effective_date"]).dt.date
            self._universe |= set(self._actions["symbol"].astype(str))
        self._last_transition = last_adjusted_date(divisor_state, self.session)
        self._calib_date = trading_date(divisor_state.reference_market_ts, self.session)
        self._working: dict[str, Constituent] = {
            c.symbol: c for c in active_constituents(self._all, self._last_transition)}
        ver = divisor_state.constituents_version or ""
        if (ver.startswith("sha1:") and not any(a.get("type") == "adjustment" for a in divisor_state.adjustments)
                and ver != constituents_fingerprint(self._working.values())):
            warnings.warn("divisor_state was calibrated with different constituent N/H/K "
                          f"({ver} != {constituents_fingerprint(self._working.values())})", stacklevel=2)
        self._sym: dict[str, _SymState] = {}
        self._pending = pd.DataFrame()
        self._last_grid: pd.Timestamp | None = None
        self._current_date: date | None = None
        self._adjust_error: str = ""
        self._latency: str | None = None
        self._sources: set[str] = set()
        self._official = _empty_official()
        self._official_source = ""
        self._official_latency = ""
        self.late_bars = 0
        self.ignored_symbols: set[str] = set()
        self.events: list[dict] = []
        self.base_sources: dict[str, dict] = {}
        self._daily_closes = normalize_daily_closes(daily_closes, index_symbol, self.session)
        if official is not None:
            self.add_official(official)

    # ------------------------------------------------------------------ input
    def _check_latency(self, classes: set[str], what: str):
        classes = {c for c in classes if c}
        if len(classes) > 1:
            raise LatencyMixError(f"{what} mix latency classes {sorted(classes)}")
        if classes:
            c = next(iter(classes))
            if self._latency is None:
                self._latency = c
            elif c != self._latency:
                raise LatencyMixError(f"{what} latency {c!r} != engine latency {self._latency!r}")

    def add_official(self, official):
        s, src, lat = _official_series(official, self.index_symbol)
        if lat:
            self._check_latency(set(lat.split(";")), "official series")
        if src:
            self._official_source = ";".join(sorted(set(filter(None, self._official_source.split(";")))
                                                    | set(src.split(";"))))
        if s.empty:
            return
        self._official = s if self._official.empty else pd.concat([self._official, s])
        self._official = self._official[~self._official.index.duplicated(keep="last")].sort_index()

    def update(self, new_bars: pd.DataFrame, official=None, final: bool = False) -> pd.DataFrame:
        if official is not None:
            self.add_official(official)
        if new_bars is not None and len(new_bars):
            if "symbol" in new_bars.columns and (new_bars["symbol"] == self.index_symbol).any():
                self.add_official(new_bars[new_bars["symbol"] == self.index_symbol])
                new_bars = new_bars[new_bars["symbol"] != self.index_symbol]
            df = _norm_bars(new_bars)
            unknown = set(df["symbol"]) - self._universe
            self.ignored_symbols |= unknown
            df = df[df["symbol"].isin(self._universe)]
            self._check_latency(set(df["latency_class"]), "price bars")
            self._sources |= set(df["source"]) - {""}
            if self._last_grid is not None:
                late = df[df["market_ts"] <= self._last_grid]
                if len(late):
                    self.late_bars += len(late)
                    for r in late.itertuples(index=False):
                        st = self._sym.setdefault(r.symbol, _SymState())
                        if st.obs_ts is None or r.market_ts > st.obs_ts:
                            self._apply_bar(st, r, jumps=None)
                    df = df[df["market_ts"] > self._last_grid]
            if len(df):
                self._pending = df if self._pending.empty else pd.concat([self._pending, df])
        return self._process(final)

    def flush(self) -> pd.DataFrame:
        return self._process(final=True)

    # -------------------------------------------------------------- internals
    def _apply_bar(self, st: _SymState, r, jumps: set | None):
        vol = r.volume
        traded = (vol is None) or (isinstance(vol, float) and math.isnan(vol)) or vol > 0
        st.obs_ts = r.market_ts
        if traded:
            if (jumps is not None and st.price and self.jump_pct is not None
                    and abs(r.close / st.price - 1.0) > self.jump_pct + 1e-9):
                jumps.add(r.symbol)
            st.price = float(r.close)
            st.trade_ts = r.market_ts
            return False
        if self.zero_volume_price_updates and (st.price is None or abs(r.close / st.price - 1.0) > 1e-9):
            # zero-volume bar carrying a new price (e.g. auction print): price
            # update, but NOT a trade for staleness purposes
            st.price = float(r.close)
            return True
        return False

    def _grid_of(self, ts: pd.Series) -> pd.Series:
        return ts.dt.ceil(self._grid_str)

    def _process(self, final: bool) -> pd.DataFrame:
        if self._pending.empty:
            return pd.DataFrame(columns=RESULT_COLUMNS)
        pend = self._pending.sort_values("market_ts", kind="stable").reset_index(drop=True)
        g = self._grid_of(pend["market_ts"])
        if final:
            ready = pd.Series(True, index=pend.index)
        else:
            ready = g < g.max()
        todo, self._pending = pend[ready], pend[~ready]
        gtodo = g[ready]
        rows = []
        for gt, chunk in todo.groupby(gtodo, sort=True):
            rows.extend(self._step(gt, chunk))
        if rows:
            self._last_grid = rows[-1]["market_ts"] if self._last_grid is None else max(
                self._last_grid, rows[-1]["market_ts"])
        out = pd.DataFrame(rows, columns=RESULT_COLUMNS)
        return self._attach(out)

    def _reference_closes(self, d: date) -> dict[str, float]:
        """Official previous-session closes (from ``daily_closes``) for day ``d``."""
        if not self._daily_closes:
            return {}
        prev = [x for x in self._daily_closes if x < d]
        if not prev:
            return {}
        ref_date = max(prev)
        closes = self._daily_closes[ref_date]
        syms = set(self._working) | set(self._sym)
        used = sorted(s for s in syms if s in closes)
        self.base_sources[d.isoformat()] = {
            "reference_date": ref_date.isoformat(), "official_close": used,
            "fallback_last_intraday": sorted(s for s in self._working if s not in closes),
            "engine_prev_date": self._current_date.isoformat() if self._current_date else None,
        }
        return {s: closes[s] for s in used}

    def _maybe_roll_day(self, d: date):
        if self._current_date is not None and d <= self._current_date:
            return
        ref = self._reference_closes(d)
        for s, p in ref.items():
            # the official close (incl. closing auction) replaces the last intraday
            # price: it is the true last price and is known before day d starts
            self._sym.setdefault(s, _SymState()).price = p
        if d > self._last_transition:
            self._transition(d)
        if self._current_date is not None or ref:
            for s, st in self._sym.items():
                if st.price is not None and (self._current_date is not None or s in ref):
                    st.base = st.price
        if not ref and self._daily_closes and self._current_date is not None:
            self.base_sources[d.isoformat()] = {"reference_date": None, "official_close": [],
                                                "fallback_last_intraday": sorted(self._working)}
        self._current_date = d

    def _transition(self, new_date: date):
        old = list(self._working.values())
        acts = None
        if self._actions is not None:
            m = (self._actions["effective_date"] > self._last_transition) & (
                self._actions["effective_date"] <= new_date)
            acts = self._actions[m]
        old_file = {c.symbol: c for c in active_constituents(self._all, self._last_transition)}
        new_file = {c.symbol: c for c in active_constituents(self._all, new_date)}
        new_def = None
        if old_file != new_file:
            # apply only the file-level differences on top of the action-updated working set
            new_def = {s: c for s, c in self._working.items() if s in new_file}
            for s, c in new_file.items():
                if old_file.get(s) != c:
                    new_def[s] = c
            new_def = list(new_def.values())
        if (acts is None or acts.empty) and new_def is None:
            self._last_transition = new_date
            return
        close = {s: st.price for s, st in self._sym.items() if st.price is not None}
        try:
            state, new_list, adj_prices, notes = apply_index_events(
                self.divisor_state, old, close, new_date, actions=acts, new_constituents=new_def)
        except (DivisorAdjustmentError, KeyError) as e:
            self._adjust_error = f"divisor_adjustment_failed:{e}"
            self.events.append({"date": new_date.isoformat(), "error": str(e)})
            self._last_transition = new_date
            return
        self._adjust_error = ""
        self.divisor_state = state
        self._working = {c.symbol: c for c in new_list}
        for s, p in adj_prices.items():
            st = self._sym.get(s)
            if st is not None and st.price is not None and p != close.get(s):
                st.price = p   # e.g. bonus: theoretical ex price until first trade
        self.events.append({"date": new_date.isoformat(), "notes": notes,
                            "divisor": state.divisor})
        self._last_transition = new_date

    def _step(self, gt: pd.Timestamp, chunk: pd.DataFrame) -> list[dict]:
        d = trading_date(gt, self.session)
        self._maybe_roll_day(d)
        jumps: set[str] = set()
        zero_vol_updates: set[str] = set()
        new_syms = set()
        for r in chunk.itertuples(index=False):
            st = self._sym.setdefault(r.symbol, _SymState())
            if self._apply_bar(st, r, jumps):
                zero_vol_updates.add(r.symbol)
            new_syms.add(r.symbol)
        n_new = len(new_syms & set(self._working))
        if self.only_new and n_new == 0:
            return []
        return [self._evaluate(gt, d, n_new, jumps, zero_vol_updates)]

    def _evaluate(self, gt, d, n_new, jumps, zero_vol_updates) -> dict:
        phase = session_phase(gt, self.session)
        hard, soft = [], []
        missing, stale, terms, stal = [], [], {}, {}
        limit_breach = []
        for s, c in self._working.items():
            st = self._sym.get(s)
            if st is None or st.price is None:
                missing.append(s)
                continue
            terms[s] = mcap_term(c, st.price)
            stal[s] = (gt - st.trade_ts).total_seconds() if st.trade_ts is not None else math.inf
            if stal[s] > self.staleness_s:
                stale.append(s)
            if (self.limit_pct is not None and st.base and
                    abs(st.price / st.base - 1.0) > self.limit_pct + self.limit_tol):
                limit_breach.append(s)
        raw = sum(terms.values()) if not missing else float("nan")
        divisor = self.divisor_state.divisor
        w_stale = wstal = max_stal = float("nan")
        if terms:
            tot = sum(terms.values())
            w = {s: v / tot for s, v in terms.items()}
            w_stale = sum(w[s] for s in stale if s in w)
            finite = {s: v for s, v in stal.items() if math.isfinite(v)}
            wstal = sum(w[s] * stal[s] for s in stal) if len(finite) == len(stal) else math.inf
            max_stal = max(stal.values())
        if missing:
            hard.append("missing_price:" + ",".join(sorted(missing)))
        if phase == CLOSED:
            hard.append("outside_session")
        elif phase in AUCTION_PHASES:
            (hard if self.auction_policy == "invalid" else soft).append(f"auction:{phase}")
        if stale:
            if phase == CONTINUOUS and w_stale > self.stale_weight_tolerance:
                hard.append(f"stale>{self.staleness_s:g}s:" + ",".join(sorted(stale)))
            else:
                soft.append("stale:" + ",".join(sorted(stale)))
        js = sorted(jumps & set(self._working))
        if js:
            hard.append(f"price_jump>{self.jump_pct:.0%}:" + ",".join(js))
        if limit_breach:
            hard.append("outside_price_limit:" + ",".join(sorted(limit_breach)))
        if self._adjust_error:
            hard.append(self._adjust_error)
        zv = sorted(zero_vol_updates & set(self._working))
        if zv:
            soft.append("zero_volume_price_update:" + ",".join(zv))
        if d < self._calib_date:
            soft.append("before_calibration_date")
        unchecked = round_index(raw / divisor) if math.isfinite(raw) else float("nan")
        valid = not hard
        return {
            "market_ts": gt, "calc_value": unchecked if valid else float("nan"),
            "raw_mcap": raw, "divisor": divisor, "n_components": len(terms),
            "n_stale": len(stale), "max_staleness_s": max_stal,
            "missing_symbols": ";".join(sorted(missing)), "stale_symbols": ";".join(sorted(stale)),
            "valid": valid, "invalid_reason": "|".join(hard),
            "latency_class": self._latency or "", "price_source": ";".join(sorted(self._sources)),
            "official_value": float("nan"), "official_source": "", "diff_pts": float("nan"),
            "diff_pct": float("nan"),
            "session_phase": phase, "flags": "|".join(soft), "calc_value_unchecked": unchecked,
            "weighted_staleness_s": wstal, "stale_weight": w_stale, "n_new": n_new,
        }

    def _attach(self, out: pd.DataFrame) -> pd.DataFrame:
        return attach_official(out, self._official, self._official_source)


def normalize_daily_closes(daily_closes, index_symbol: str = "XU030",
                           session: SessionConfig | None = None) -> dict[date, dict[str, float]]:
    """Official session closes -> {Istanbul date: {symbol: close}}.

    Accepts (a) a bars frame (BAR_COLUMNS, interval "1d"): the session date is
    taken from ``bar_start`` (else a ``date`` column, else ``market_ts``), in
    Istanbul time; or (b) a wide frame, index = dates, columns = symbols.
    Volume is ignored (Yahoo 1d reports volume 0 for e.g. SASA; the close is
    valid). Latency class is NOT checked: these are reference closes, not
    calculation prices.
    """
    if daily_closes is None or len(daily_closes) == 0:
        return {}
    session = session or SessionConfig()
    out: dict[date, dict[str, float]] = {}
    df = daily_closes
    if "symbol" in df.columns and "close" in df.columns:
        df = df[df["symbol"] != index_symbol]
        if "bar_start" in df.columns:
            dts = pd.to_datetime(df["bar_start"], utc=True).dt.tz_convert(session.tz).dt.date
        elif "date" in df.columns:
            dts = pd.to_datetime(df["date"]).dt.date
        else:
            dts = pd.to_datetime(df["market_ts"], utc=True).dt.tz_convert(session.tz).dt.date
        close = pd.to_numeric(df["close"], errors="coerce")
        for d, sym, c in zip(dts, df["symbol"].astype(str), close):
            if c is not None and math.isfinite(c) and c > 0:
                out.setdefault(d, {})[sym] = float(c)
        return out
    for idx, row in df.iterrows():
        d = pd.Timestamp(idx).date()
        for sym, c in row.items():
            c = pd.to_numeric(c, errors="coerce")
            if pd.notna(c) and c > 0:
                out.setdefault(d, {})[str(sym)] = float(c)
    return out


def attach_official(result: pd.DataFrame, official, official_source: str = "",
                    index_symbol: str = "XU030") -> pd.DataFrame:
    """Join the official index by EXACT market_ts (no nearest matching)."""
    out = result.copy()
    if isinstance(official, pd.Series) and official_source:
        s = official
    else:
        s, src, _ = _official_series(official, index_symbol)
        official_source = official_source or src
    if out.empty:
        return out
    if s.empty:
        out["official_value"] = float("nan")
        return out
    ts = pd.DatetimeIndex(pd.to_datetime(out["market_ts"], utc=True))
    vals = s.reindex(ts).to_numpy(dtype=float)
    out["official_value"] = vals
    out["official_source"] = np.where(np.isfinite(vals), official_source, "")
    calc = out["calc_value"].astype(float).to_numpy()
    out["diff_pts"] = calc - vals
    out["diff_pct"] = (calc - vals) / vals * 100.0
    return out


def compute_index_series(bars: pd.DataFrame, constituents: list[Constituent], divisor_state: DivisorState,
                         official=None, grid: str = "1min", staleness_s: float = 300,
                         **engine_kwargs) -> pd.DataFrame:
    """Batch calculation (same code path as ``IndexEngine``).

    Returns INDEX_RESULT_COLUMNS + EXTRA_RESULT_COLUMNS. If ``official`` is None
    and ``bars`` contains the index symbol, those rows are used as official.
    The (possibly rule-5.1-adjusted) divisor state is in ``df.attrs["divisor_state"]``.
    """
    eng = IndexEngine(constituents, divisor_state, grid=grid, staleness_s=staleness_s,
                      official=official, **engine_kwargs)
    res = eng.update(bars, final=True)
    res = res.reset_index(drop=True)
    res.attrs["divisor_state"] = eng.divisor_state
    res.attrs["events"] = eng.events
    res.attrs["base_sources"] = eng.base_sources
    res.attrs["ignored_symbols"] = sorted(eng.ignored_symbols)
    return res
