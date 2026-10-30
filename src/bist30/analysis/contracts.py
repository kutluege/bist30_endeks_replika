"""VİOP BIST 30 index futures: contract specification, calendar and final settlement.

Specification source (checked 2026-10-30):
  * Borsa İstanbul, "Index Futures" page
    https://borsaistanbul.com/en/markets/viop/futures/index-futures
  * Borsa İstanbul Derivatives Market Procedure, Revision 70 (01/07/2026),
    section "D. BIST 30 Futures Contract" and Appendix-3 (trading hours)
    https://www.borsaistanbul.com/files/derivatives-market-procedure.pdf
  * Equity market trading hours (continuous auction 10:00-18:00, closing call
    18:01-18:05, uncross 18:05) https://www.borsaistanbul.com/en/markets/equity-market/trading-hours

Summary (see docs/analysis.md for the full Turkish write-up):
  underlying          BIST 30 price index (XU030)
  contract size       10 x index  ->  multiplier = TRY 10 per index point
  quotation / tick    index value, 2 decimals, minimum tick 1.00 point (= TRY 10)
  months              Feb, Apr, Jun, Aug, Oct, Dec; three nearest trade + extra Dec
  last trading day    last business day of the month; if that is a half day
                      (official holiday eve) -> preceding business day
  settlement          cash, T+1
  daily settlement    volume-weighted avg of trades in last 10 min of normal session
                      (fallbacks: last 10 trades, all trades, previous settlement)
  final settlement    0.8 * TWAP(index, last 30 min of continuous auction)
                      + 0.2 * index closing value, rounded to nearest tick
  sessions            normal 09:30-18:10 (opening call 09:20-09:25),
                      evening 19:00-23:00; half day 09:30-12:40, no evening session;
                      no evening session on the last trading day of the year
  price limits        +-15 % normal session (applied as +-10 % since ann. 2020/20),
                      +-3 % evening session, around previous daily settlement
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from bist30.models import ISTANBUL_TZ, FuturesContract

# --------------------------------------------------------------------------
# Specification constants
# --------------------------------------------------------------------------
XU030_MULTIPLIER = 10.0     # TRY per index point
XU030_TICK = 1.0            # index points
EXPIRY_MONTHS = (2, 4, 6, 8, 10, 12)
N_CONCURRENT = 3

NORMAL_SESSION = (time(9, 30), time(18, 10))
HALF_DAY_SESSION = (time(9, 30), time(12, 40))
EVENING_SESSION = (time(19, 0), time(23, 0))
EQUITY_CONTINUOUS = (time(10, 0), time(18, 0))
EQUITY_CONTINUOUS_HALF_DAY = (time(10, 0), time(12, 30))  # equity half day ends 12:30 (closing call after)
FSP_WINDOW_MINUTES = 30
FSP_TWAP_WEIGHT = 0.8
FSP_CLOSE_WEIGHT = 0.2
PRICE_LIMIT_NORMAL = 0.10   # +-15 % in the procedure, applied as 10 % (announcement 2020/20)
PRICE_LIMIT_EVENING = 0.03

SPEC_SOURCES = {
    "index_futures_page": "https://borsaistanbul.com/en/markets/viop/futures/index-futures",
    "viop_procedure": "https://www.borsaistanbul.com/files/derivatives-market-procedure.pdf",
    "final_settlement": "https://borsaistanbul.com/en/markets/viop/contract-specifications/final-settlement-prices",
    "equity_hours": "https://www.borsaistanbul.com/en/markets/equity-market/trading-hours",
}

# --------------------------------------------------------------------------
# Turkish market calendar
# --------------------------------------------------------------------------
# Fixed public holidays (month, day). 28 October is a half day (Republic Day eve).
_FIXED_HOLIDAYS = [(1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29)]
_FIXED_HALF_DAYS = [(10, 28)]

# Religious holidays (Ramazan / Kurban Bayramı) - full days and eves (half days).
# Dates from the Diyanet calendar; VERIFY against Borsa İstanbul's annual holiday
# announcement and add any government "bridge" closures via `extra_holidays`.
_RELIGIOUS_HOLIDAYS: dict[int, list[str]] = {
    2024: ["2024-04-10", "2024-04-11", "2024-04-12", "2024-06-16", "2024-06-17", "2024-06-18", "2024-06-19"],
    2025: ["2025-03-30", "2025-03-31", "2025-04-01", "2025-06-06", "2025-06-07", "2025-06-08", "2025-06-09"],
    2026: ["2026-03-20", "2026-03-21", "2026-03-22", "2026-05-27", "2026-05-28", "2026-05-29", "2026-05-30"],
    2027: ["2027-03-09", "2027-03-10", "2027-03-11", "2027-05-16", "2027-05-17", "2027-05-18", "2027-05-19"],
}
_RELIGIOUS_HALF_DAYS: dict[int, list[str]] = {
    2024: ["2024-04-09", "2024-06-15"],
    2025: ["2025-03-29", "2025-06-05"],
    2026: ["2026-03-19", "2026-05-26"],
    2027: ["2027-03-08", "2027-05-15"],
}


@dataclass(frozen=True)
class MarketCalendar:
    """Borsa İstanbul business-day calendar (weekends + holidays + half days)."""

    extra_holidays: frozenset = frozenset()
    extra_half_days: frozenset = frozenset()

    def holidays(self, year: int) -> set[date]:
        out = {date(year, m, d) for m, d in _FIXED_HOLIDAYS}
        out |= {date.fromisoformat(s) for s in _RELIGIOUS_HOLIDAYS.get(year, [])}
        out |= {d for d in self.extra_holidays if d.year == year}
        return out

    def half_days(self, year: int) -> set[date]:
        out = {date(year, m, d) for m, d in _FIXED_HALF_DAYS}
        out |= {date.fromisoformat(s) for s in _RELIGIOUS_HALF_DAYS.get(year, [])}
        out |= {d for d in self.extra_half_days if d.year == year}
        return out - self.holidays(year)

    def calendar_known(self, year: int) -> bool:
        return year in _RELIGIOUS_HOLIDAYS

    def is_business_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays(d.year)

    def is_half_day(self, d: date) -> bool:
        return self.is_business_day(d) and d in self.half_days(d.year)

    def previous_business_day(self, d: date) -> date:
        d = d - timedelta(days=1)
        while not self.is_business_day(d):
            d -= timedelta(days=1)
        return d

    def business_days_between(self, start: date, end: date) -> int:
        """Number of business days in (start, end]."""
        if end <= start:
            return 0
        n, d = 0, start
        while d < end:
            d += timedelta(days=1)
            if self.is_business_day(d):
                n += 1
        return n


DEFAULT_CALENDAR = MarketCalendar()


def last_trading_day(year: int, month: int, calendar: MarketCalendar = DEFAULT_CALENDAR) -> date:
    """Last business day of the month; preceding business day if that is a half day."""
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    while not calendar.is_business_day(d):
        d -= timedelta(days=1)
    if calendar.is_half_day(d):
        d = calendar.previous_business_day(d)
    return d


# --------------------------------------------------------------------------
# Contracts
# --------------------------------------------------------------------------
_CODE_RE = re.compile(r"^F_(?P<und>[A-Z0-9]+?)(?P<mini>M?)(?P<mm>\d{2})(?P<yy>\d{2})$")


def contract_code(year: int, month: int, underlying: str = "XU030") -> str:
    return f"F_{underlying}{month:02d}{year % 100:02d}"


def contract_from_code(code: str, calendar: MarketCalendar = DEFAULT_CALENDAR) -> FuturesContract:
    """Parse a VİOP futures code such as ``F_XU0301226`` (Dec 2026)."""
    m = _CODE_RE.match(code.strip().upper())
    if not m:
        raise ValueError(f"unrecognised VİOP futures code: {code!r}")
    und, mm, yy = m.group("und"), int(m.group("mm")), int(m.group("yy"))
    if m.group("mini"):
        raise ValueError(f"{code}: non-standard ('M') contract size codes are not supported")
    if und != "XU030":
        raise ValueError(f"{code}: only BIST 30 (XU030) futures are specified in this module")
    if mm not in EXPIRY_MONTHS:
        raise ValueError(f"{code}: month {mm} is not a BIST 30 expiry month {EXPIRY_MONTHS}")
    year = 2000 + yy
    return FuturesContract(
        code=contract_code(year, mm, und),
        underlying=und,
        expiry=last_trading_day(year, mm, calendar),
        multiplier=XU030_MULTIPLIER,
        tick_size=XU030_TICK,
    )


def _to_date(as_of) -> date:
    if isinstance(as_of, pd.Timestamp):
        ts = as_of if as_of.tzinfo is None else as_of.tz_convert(ISTANBUL_TZ)
        return ts.date()
    if isinstance(as_of, datetime):
        if as_of.tzinfo is not None:
            return pd.Timestamp(as_of).tz_convert(ISTANBUL_TZ).date()
        return as_of.date()
    return as_of


def active_contracts(as_of, calendar: MarketCalendar = DEFAULT_CALENDAR,
                     underlying: str = "XU030") -> list[FuturesContract]:
    """Contracts listed on ``as_of`` (Istanbul date): the three nearest expiry months
    whose last trading day is >= as_of, plus December if it is not among them."""
    d = _to_date(as_of)
    out: list[FuturesContract] = []
    y, m = d.year, d.month
    while len(out) < N_CONCURRENT:
        if m in EXPIRY_MONTHS:
            c = contract_from_code(contract_code(y, m, underlying), calendar)
            if c.expiry >= d:
                out.append(c)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    if not any(c.expiry.month == 12 for c in out):
        yd = d.year if contract_from_code(contract_code(d.year, 12), calendar).expiry >= d else d.year + 1
        out.append(contract_from_code(contract_code(yd, 12, underlying), calendar))
    return sorted(out, key=lambda c: c.expiry)


def front_contract(as_of, calendar: MarketCalendar = DEFAULT_CALENDAR) -> FuturesContract:
    return active_contracts(as_of, calendar)[0]


def expiry_timestamp(contract: FuturesContract) -> pd.Timestamp:
    """UTC timestamp at which the final settlement reference ends: the end of the
    equity continuous auction (18:00 Istanbul) on the last trading day."""
    return pd.Timestamp(datetime.combine(contract.expiry, EQUITY_CONTINUOUS[1]), tz=ISTANBUL_TZ).tz_convert("UTC")


def round_to_tick(x, tick: float = XU030_TICK):
    """Round half away from zero to the nearest tick (exchange style)."""
    a = np.asarray(x, dtype=float) / tick
    r = np.sign(a) * np.floor(np.abs(a) + 0.5) * tick
    return float(r) if np.ndim(r) == 0 else r


def price_limits(base_price: float, session: str = "normal", tick: float = XU030_TICK,
                 limit: Optional[float] = None) -> tuple[float, float]:
    """(lower, upper) daily price limits; upper rounded down, lower rounded up to tick."""
    pct = limit if limit is not None else (PRICE_LIMIT_EVENING if session == "evening" else PRICE_LIMIT_NORMAL)
    base = round_to_tick(base_price, tick)
    up, lo = base * (1 + pct), base * (1 - pct)
    return float(np.ceil(lo / tick - 1e-9) * tick), float(np.floor(up / tick + 1e-9) * tick)


def daily_settlement_price(trades: pd.DataFrame, session_end: pd.Timestamp,
                           previous_settlement: Optional[float] = None,
                           tick: float = XU030_TICK) -> tuple[float, str]:
    """Daily settlement per VİOP procedure from a trade tape.

    ``trades`` columns: ``market_ts`` (UTC), ``price``, ``qty``. Returns (price, method).
    Requires trade-level data (not available from free sources - see docs)."""
    t = trades[trades["market_ts"] <= session_end].sort_values("market_ts")
    last10m = t[t["market_ts"] > session_end - pd.Timedelta(minutes=10)]
    if len(last10m) >= 10:
        sel, how = last10m, "vwap_last_10min"
    elif len(t) >= 10:
        sel, how = t.tail(10), "vwap_last_10_trades"
    elif len(t) > 0:
        sel, how = t, "vwap_all_trades"
    elif previous_settlement is not None:
        return float(previous_settlement), "previous_settlement"
    else:
        return float("nan"), "no_trades_no_previous"
    vwap = float((sel["price"] * sel["qty"]).sum() / sel["qty"].sum())
    return round_to_tick(vwap, tick), how


def final_settlement_price(index_values: pd.Series, closing_value: float, expiry: date,
                           tick: float = XU030_TICK, window_minutes: int = FSP_WINDOW_MINUTES,
                           window_end: time = EQUITY_CONTINUOUS[1],
                           min_coverage: float = 0.9) -> dict:
    """Replicate the final settlement price.

    ``index_values``: Series of disseminated XU030 values indexed by UTC tz-aware
    timestamps (value valid from its timestamp until the next one). The TWAP covers
    [end - 30 min, end) in Istanbul time of ``expiry`` (end = 18:00, end of continuous
    auction). ``closing_value`` is the official index close (after the closing call).

    Returns dict(fsp, twap, close, coverage, n_obs, status). With 1-minute bars
    (bar END timestamps) the TWAP is an approximation of the exchange's
    tick-by-tick TWAP and is flagged as such in ``status``.
    """
    end = pd.Timestamp(datetime.combine(expiry, window_end), tz=ISTANBUL_TZ).tz_convert("UTC")
    start = end - pd.Timedelta(minutes=window_minutes)
    s = index_values.dropna().sort_index()
    if s.index.tz is None:
        raise ValueError("index_values must have a tz-aware index (UTC)")
    # value in force at `start` = last observation at or before start
    before = s[s.index <= start]
    inside = s[(s.index > start) & (s.index < end)]
    pts = pd.concat([before.tail(1).set_axis([start]), inside]) if len(before) else inside
    if len(pts) == 0:
        return dict(fsp=float("nan"), twap=float("nan"), close=closing_value, coverage=0.0,
                    n_obs=0, status="not feasible: no index values in settlement window")
    t = pts.index.append(pd.DatetimeIndex([end]))
    dur = np.asarray((t[1:] - t[:-1]).total_seconds(), dtype=float)
    twap = float(np.sum(pts.to_numpy(dtype=float) * dur) / np.sum(dur))
    coverage = float(np.sum(dur) / (window_minutes * 60.0))
    if not np.isfinite(closing_value):
        return dict(fsp=float("nan"), twap=twap, close=closing_value, coverage=coverage,
                    n_obs=int(len(pts)), status="not feasible: closing value missing")
    fsp = round_to_tick(FSP_TWAP_WEIGHT * twap + FSP_CLOSE_WEIGHT * closing_value, tick)
    status = "ok" if coverage >= min_coverage else f"low coverage {coverage:.0%}"
    med_gap = float(np.median(dur)) if len(dur) else float("nan")
    if med_gap > 5:
        status += f" (approximation: median sampling {med_gap:.0f}s vs exchange tick-by-tick TWAP)"
    return dict(fsp=fsp, twap=twap, close=float(closing_value), coverage=coverage,
                n_obs=int(len(pts)), status=status)


def in_session(ts: pd.Series | pd.DatetimeIndex, session: str = "normal",
               calendar: MarketCalendar = DEFAULT_CALENDAR) -> np.ndarray:
    """Boolean mask: UTC timestamps inside the VİOP normal / evening session."""
    idx = pd.DatetimeIndex(ts).tz_convert(ISTANBUL_TZ)
    out = np.zeros(len(idx), dtype=bool)
    for i, t in enumerate(idx):
        d = t.date()
        if not calendar.is_business_day(d):
            continue
        tt = t.time()
        if session == "evening":
            if calendar.is_half_day(d):
                continue
            out[i] = EVENING_SESSION[0] <= tt <= EVENING_SESSION[1]
        else:
            lo, hi = HALF_DAY_SESSION if calendar.is_half_day(d) else NORMAL_SESSION
            out[i] = lo <= tt <= hi
    return out
