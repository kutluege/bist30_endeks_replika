"""Cost-of-carry fair value for VİOP BIST 30 futures.

Convention (default, configurable):
  * Day count ACT/365 on calendar time, measured to the second from the
    observation's ``market_ts`` to the end of the final-settlement reference window
    (18:00 Istanbul on the last trading day, see ``contracts.expiry_timestamp``).
  * Simple (money-market) compounding: TRY money-market references (TLREF, TCMB
    policy/O-N rates, deposit and repo quotes) are quoted as simple annual rates on
    ACT/365, and every listed contract matures within ~10 months, so
        F = S * (1 + r*tau) - sum_i D_i * (1 + r*tau_i)
    is the natural no-arbitrage price for a cash-and-carry funded at that rate.
    ``compounding="continuous"`` (F = S*e^{(r-q)tau} - sum D_i e^{r tau_i}) and
    ``"annual"`` are available for comparison; note a continuous rate is not the
    quoted number (r_c = ln(1 + r_s*tau)/tau), so feeding a quoted TL rate into the
    continuous formula overstates fair value at high TL rates.
  * Dividends: either a simple dividend yield ``div_yield`` (decimal p.a.) or an
    explicit schedule of expected dividends in INDEX POINTS with ex-dates
    (``dividends``), future-valued to expiry. A price index (XU030) drops on ex-dates,
    so expected dividends reduce fair value.
  * Rates are decimals (0.40 = 40 %). Nothing is guessed: ``rate=None`` yields NaN.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Optional, Sequence, Union

import numpy as np
import pandas as pd

from bist30.models import ISTANBUL_TZ, FuturesContract

from ._common import to_utc
from .contracts import DEFAULT_CALENDAR, EQUITY_CONTINUOUS, MarketCalendar, expiry_timestamp

SECONDS_PER_YEAR_365 = 365.0 * 86400.0
COMPOUNDING = ("simple", "continuous", "annual")
DAY_COUNTS = ("ACT/365", "BUS/252")

RateLike = Union[float, int, pd.Series, None]


def _check_comp(compounding: str) -> None:
    if compounding not in COMPOUNDING:
        raise ValueError(f"compounding must be one of {COMPOUNDING}, got {compounding!r}")


def year_fraction(ts: pd.DatetimeIndex | pd.Series, contract: FuturesContract,
                  day_count: str = "ACT/365", calendar: MarketCalendar = DEFAULT_CALENDAR) -> np.ndarray:
    """Time to expiry in years for each UTC timestamp (negative after expiry -> NaN)."""
    idx = pd.DatetimeIndex(to_utc(pd.DatetimeIndex(ts)))
    end = expiry_timestamp(contract)
    if day_count == "ACT/365":
        tau = np.asarray((end - idx).total_seconds(), dtype=float) / SECONDS_PER_YEAR_365
    elif day_count == "BUS/252":
        loc = idx.tz_convert(ISTANBUL_TZ)
        s0, s1 = EQUITY_CONTINUOUS
        sess = (s1.hour * 60 + s1.minute) - (s0.hour * 60 + s0.minute)
        tau = np.empty(len(idx))
        for i, t in enumerate(loc):
            d = t.date()
            full = calendar.business_days_between(d, contract.expiry)
            mins = (s1.hour * 60 + s1.minute) - (t.hour * 60 + t.minute + t.second / 60)
            frac = min(max(mins / sess, 0.0), 1.0) if calendar.is_business_day(d) else 0.0
            tau[i] = (full + frac) / 252.0 if d <= contract.expiry else -1.0
    else:
        raise ValueError(f"day_count must be one of {DAY_COUNTS}")
    tau = np.asarray(tau, dtype=float)
    tau[tau < 0] = np.nan
    return tau


def align_rate(rate: RateLike, index: pd.DatetimeIndex) -> np.ndarray:
    """Float -> constant; Series indexed by date/timestamp -> as-of (backward) aligned.
    Raises if the rate looks like a percentage (>|2|)."""
    n = len(index)
    if rate is None:
        return np.full(n, np.nan)
    if np.isscalar(rate):
        r = float(rate)
        if abs(r) > 2:
            raise ValueError(f"rate {r} looks like a percentage; pass decimals (0.40 = 40 %)")
        return np.full(n, r)
    s = pd.Series(rate).astype(float).dropna()
    if len(s) == 0:
        return np.full(n, np.nan)
    if (s.abs() > 2).any():
        raise ValueError("rate series looks like percentages; pass decimals")
    ri = s.index
    if not isinstance(ri, pd.DatetimeIndex):
        ri = pd.DatetimeIndex(pd.to_datetime(ri))
    if ri.tz is None:  # dates -> start of the Istanbul day
        ri = ri.tz_localize(ISTANBUL_TZ)
    s.index = ri.tz_convert("UTC")
    s = s[~s.index.duplicated(keep="last")].sort_index()
    out = s.reindex(index.union(s.index)).ffill().reindex(index)
    return out.to_numpy(dtype=float)


def _accrual(r: np.ndarray, tau: np.ndarray, compounding: str) -> np.ndarray:
    if compounding == "simple":
        return 1.0 + r * tau
    if compounding == "continuous":
        return np.exp(r * tau)
    return np.power(1.0 + r, tau)


def _dividend_schedule(dividends) -> pd.DataFrame:
    """Accept DataFrame(ex_date, points) / list of (date, points) / dict."""
    if dividends is None:
        return pd.DataFrame({"ex_date": pd.Series(dtype="object"), "points": pd.Series(dtype=float)})
    if isinstance(dividends, dict):
        dividends = list(dividends.items())
    if not isinstance(dividends, pd.DataFrame):
        dividends = pd.DataFrame(list(dividends), columns=["ex_date", "points"])
    df = dividends[["ex_date", "points"]].copy()
    df["ex_date"] = pd.to_datetime(df["ex_date"]).dt.date
    df["points"] = df["points"].astype(float)
    return df


def dividend_fv(index: pd.DatetimeIndex, contract: FuturesContract, rate: np.ndarray,
                dividends=None, compounding: str = "simple") -> np.ndarray:
    """Future value at expiry of expected dividend points with ex-date in
    (valuation Istanbul date, expiry]."""
    sched = _dividend_schedule(dividends)
    out = np.zeros(len(index))
    if len(sched) == 0:
        return out
    end = expiry_timestamp(contract)
    vdates = pd.DatetimeIndex(index).tz_convert(ISTANBUL_TZ).date
    for ex, pts in zip(sched["ex_date"], sched["points"]):
        if ex > contract.expiry:
            continue
        ex_ts = pd.Timestamp(datetime.combine(ex, EQUITY_CONTINUOUS[0]), tz=ISTANBUL_TZ).tz_convert("UTC")
        tau_i = max((end - ex_ts).total_seconds() / SECONDS_PER_YEAR_365, 0.0)
        live = np.array([vd < ex for vd in vdates])
        out += np.where(live, pts * _accrual(rate, np.full(len(index), tau_i), compounding), 0.0)
    return out


def theoretical_futures(spot: pd.Series, contract: FuturesContract, rate: RateLike,
                        div_yield: float = 0.0, dividends=None, compounding: str = "simple",
                        day_count: str = "ACT/365", calendar: MarketCalendar = DEFAULT_CALENDAR) -> pd.Series:
    """Fair futures price for each spot observation.

    ``spot``: Series of index levels indexed by UTC tz-aware ``market_ts``.
    ``rate``: decimal annual rate (float) or Series (as-of aligned), quoted with
    ``compounding``. ``div_yield``: decimal p.a. (same compounding).
    ``dividends``: expected dividends in index points with ex-dates (optional).
    """
    _check_comp(compounding)
    idx = pd.DatetimeIndex(to_utc(pd.DatetimeIndex(spot.index)))
    S = spot.to_numpy(dtype=float)
    tau = year_fraction(idx, contract, day_count, calendar)
    r = align_rate(rate, idx)
    q = float(div_yield or 0.0)
    if compounding == "simple":
        F = S * (1.0 + r * tau - q * tau)
    elif compounding == "continuous":
        F = S * np.exp((r - q) * tau)
    else:
        F = S * np.power(1.0 + r, tau) / np.power(1.0 + q, tau)
    F = F - dividend_fv(idx, contract, r, dividends, compounding)
    return pd.Series(F, index=spot.index, name="fair_value")


def implied_rate(futures: np.ndarray, spot: np.ndarray, tau: np.ndarray, div_fv: np.ndarray | float = 0.0,
                 div_yield: float = 0.0, compounding: str = "simple", min_tau: float = 1.0 / 365) -> np.ndarray:
    """Annualised implied repo (financing) rate from futures and spot, in the given
    compounding. NaN where tau < ``min_tau`` (rate explodes near expiry)."""
    _check_comp(compounding)
    F = np.asarray(futures, dtype=float)
    S = np.asarray(spot, dtype=float)
    tau = np.asarray(tau, dtype=float)
    x = (F + np.asarray(div_fv, dtype=float)) / S
    with np.errstate(divide="ignore", invalid="ignore"):
        if compounding == "simple":
            r = (x - 1.0) / tau + div_yield
        elif compounding == "continuous":
            r = np.log(x) / tau + div_yield
        else:
            r = (np.power(x, 1.0 / tau) * (1.0 + div_yield)) - 1.0
    r = np.where((tau >= min_tau) & np.isfinite(r), r, np.nan)
    return r


def convert_rate(r: float, tau: float, src: str = "simple", dst: str = "continuous") -> float:
    """Convert an annual rate between compounding conventions over horizon tau."""
    _check_comp(src)
    _check_comp(dst)
    growth = float(_accrual(np.array([r]), np.array([tau]), src)[0])
    if dst == "simple":
        return (growth - 1.0) / tau
    if dst == "continuous":
        return float(np.log(growth) / tau)
    return float(growth ** (1.0 / tau) - 1.0)
