"""Borsa Istanbul equity-market session phases (Pay Piyasasi).

Times are Europe/Istanbul local. A grid point ``t`` is a bar END, i.e. it
summarises the interval (t - grid, t]; phases are therefore assigned with
"start < t <= end" semantics.

Schedule (full day) used here -- verified against Yahoo 1m bars of
2026-10-21..30 where possible, otherwise from BIST public session tables:

  09:40-09:55  opening auction, order collection (no trades)
  09:55-10:00  opening auction matching / opening price print (Yahoo: 09:55 bar)
  10:00-18:00  continuous trading
  18:00-18:05  closing auction (closing price print at a random end ~18:05)
  18:05-18:10  trades at the closing price
Half day (arife):  continuous 10:00-12:30, closing auction 12:30-12:35,
                   trades at close 12:35-12:40 (Yahoo 2026-10-28 shows the
                   closing print in the 12:35 bar).
NOTE: Yahoo 1m bars of FULL days stop at the 17:59 bar (ends 18:00); the
closing-auction print is missing there and only appears in the daily bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import pandas as pd

from bist30.models import ISTANBUL_TZ

CLOSED = "closed"
OPENING_AUCTION = "opening_auction"
CONTINUOUS = "continuous"
CLOSING_AUCTION = "closing_auction"
CLOSING_TRADES = "closing_trades"   # trades at the closing price
AUCTION_PHASES = frozenset({OPENING_AUCTION, CLOSING_AUCTION, CLOSING_TRADES})

# Known half trading days (arife). Verify each year against the BIST calendar.
DEFAULT_HALF_DAYS = frozenset({
    date(2025, 3, 29), date(2025, 6, 5), date(2025, 10, 28),
    date(2026, 3, 19), date(2026, 5, 26), date(2026, 10, 28),
})


@dataclass(frozen=True)
class SessionConfig:
    tz: str = ISTANBUL_TZ
    opening_auction_start: time = time(9, 40)
    continuous_start: time = time(10, 0)
    continuous_end: time = time(18, 0)
    closing_auction_minutes: int = 5
    closing_trades_minutes: int = 5
    half_day_continuous_end: time = time(12, 30)
    half_days: frozenset = field(default_factory=lambda: DEFAULT_HALF_DAYS)
    holidays: frozenset = field(default_factory=frozenset)

    def continuous_end_for(self, d: date) -> time:
        return self.half_day_continuous_end if d in self.half_days else self.continuous_end


def _to_local(ts, tz: str) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        raise ValueError(f"naive timestamp not allowed: {ts}")
    return ts.tz_convert(tz)


def trading_date(ts, session: SessionConfig | None = None) -> date:
    """Istanbul calendar date of a timestamp."""
    session = session or SessionConfig()
    return _to_local(ts, session.tz).date()


def session_phase(ts, session: SessionConfig | None = None) -> str:
    """Phase of the interval ending at ``ts`` (bar-end semantics)."""
    session = session or SessionConfig()
    lt = _to_local(ts, session.tz)
    d = lt.date()
    if lt.weekday() >= 5 or d in session.holidays:
        return CLOSED
    naive = lt.tz_localize(None).to_pydatetime()

    def at(t: time) -> datetime:
        return datetime.combine(d, t)

    cont_end = at(session.continuous_end_for(d))
    auc_end = cont_end + timedelta(minutes=session.closing_auction_minutes)
    trades_end = auc_end + timedelta(minutes=session.closing_trades_minutes)
    if naive <= at(session.opening_auction_start):
        return CLOSED
    if naive <= at(session.continuous_start):
        return OPENING_AUCTION
    if naive <= cont_end:
        return CONTINUOUS
    if naive <= auc_end:
        return CLOSING_AUCTION
    if naive <= trades_end:
        return CLOSING_TRADES
    return CLOSED
