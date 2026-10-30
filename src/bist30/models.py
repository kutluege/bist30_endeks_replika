"""Shared data contracts between the metadata, sources, engine and analysis layers.

OWNER: orchestrator. Sub-packages must not change these contracts without
coordination; add optional fields only.

Time rules (non-negotiable):
  * Every timestamp is a tz-aware ``pandas.Timestamp`` / ``datetime`` in UTC.
    Convert to Europe/Istanbul only for display.
  * ``market_ts`` = the exchange time the price refers to. For OHLC bars this is
    the bar END (bar_start + interval); the close of a bar is the last trade
    price as of ``market_ts``.
  * ``received_ts`` = wall-clock time the record reached this machine.
  * ``latency_class`` tells whether the source is realtime, 15-minute delayed,
    end-of-day, etc. Records with different latency classes are never mixed in
    one calculation or comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional

import pandas as pd

ISTANBUL_TZ = "Europe/Istanbul"
UTC = "UTC"


class InstrumentType(str, Enum):
    EQUITY = "equity"
    INDEX = "index"
    FUTURE = "future"


class LatencyClass(str, Enum):
    REALTIME = "realtime"
    DELAYED_15M = "delayed_15m"
    EOD = "eod"            # official end-of-day / bulletin data
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# Bars (the main market-data interchange format)
# --------------------------------------------------------------------------
# A "bars frame" is a pandas.DataFrame with exactly these columns (extra
# optional columns allowed: bid, ask, bid_size, ask_size, trade_count, vwap).
BAR_COLUMNS = [
    "symbol",          # canonical symbol: "AKBNK", "XU030", "F_XU0301226"
    "instrument_type", # InstrumentType value
    "bar_start",       # UTC tz-aware
    "market_ts",       # UTC tz-aware, = bar END
    "open", "high", "low", "close",
    "volume",          # may be NaN (indices on Yahoo report 0)
    "source",          # adapter name, e.g. "yahoo"
    "latency_class",   # LatencyClass value
    "interval",        # "1m", "5m", "1d", "tick"
    "received_ts",     # UTC tz-aware
]


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in BAR_COLUMNS})


@dataclass(frozen=True)
class Quote:
    """A single point-in-time price observation (snapshot / last trade)."""

    symbol: str
    instrument_type: InstrumentType
    price: float
    market_ts: datetime
    received_ts: datetime
    source: str
    latency_class: LatencyClass
    volume: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Depth snapshot (only if a free, permitted depth source exists)."""

    symbol: str
    market_ts: datetime
    received_ts: datetime
    source: str
    latency_class: LatencyClass
    bids: list[tuple[float, float]] = field(default_factory=list)  # (price, size) best first
    asks: list[tuple[float, float]] = field(default_factory=list)


# --------------------------------------------------------------------------
# Index metadata
# --------------------------------------------------------------------------
# data/metadata/constituents.csv columns (one row per constituent per period):
CONSTITUENT_COLUMNS = [
    "symbol",              # BIST code, e.g. "AKBNK"
    "name",
    "yahoo_ticker",        # e.g. "AKBNK.IS"
    "shares_outstanding",  # N_i: pay sayisi (number of shares, TL 1 nominal)
    "free_float_ratio",    # H_i as a fraction 0..1 (index-used FDPO, rounded per rule 2.19)
    "weight_factor",       # K_i, 1.0 for plain XU030 (uncapped)
    "effective_from",      # ISO date, inclusive
    "effective_to",        # ISO date, inclusive (blank = open ended)
    "shares_source",       # where N_i came from (URL / doc) + as-of date
    "ff_source",           # where H_i came from + as-of date
    "notes",
]

# data/metadata/corporate_actions.csv columns:
CORPORATE_ACTION_COLUMNS = [
    "symbol",
    "action_type",         # bonus_issue | rights_issue | cash_dividend | split | ff_change |
                           # inclusion | exclusion | merger | spinoff | share_conversion
    "announcement_date",   # KAP publication date
    "effective_date",      # index effective date per rule 5.2
    "ratio",               # e.g. bonus 100% -> 1.0 (new shares per old share)
    "cash_amount",         # TL per share (dividend gross/net or rights price)
    "new_shares_outstanding",
    "new_free_float_ratio",
    "affects_price_index", # bool: price-index divisor adjusted? (cash div -> False)
    "source_url",
    "notes",
]


@dataclass(frozen=True)
class Constituent:
    symbol: str
    name: str
    yahoo_ticker: str
    shares_outstanding: float
    free_float_ratio: float
    weight_factor: float = 1.0
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None


@dataclass
class DivisorState:
    """Persisted at data/state/divisor.json."""

    divisor: float
    reference_market_ts: datetime        # UTC
    reference_official_value: float
    reference_raw_mcap: float            # sum(P*N*H*K) at reference time
    official_source: str
    method: str                          # "official" | "calibrated"
    constituents_version: str            # hash / date of constituents file used
    adjustments: list[dict] = field(default_factory=list)  # history of rule-5.1 adjustments


# --------------------------------------------------------------------------
# Engine output
# --------------------------------------------------------------------------
# One row per evaluation time (calc_ts grid point):
INDEX_RESULT_COLUMNS = [
    "market_ts",           # UTC grid point (bar end)
    "calc_value",          # independent XU030, NaN if invalid
    "raw_mcap",            # sum(P*N*H*K)
    "divisor",
    "n_components",        # constituents with a valid price
    "n_stale",             # constituents whose last trade is older than staleness threshold
    "max_staleness_s",
    "missing_symbols",     # ";"-joined
    "stale_symbols",       # ";"-joined
    "valid",               # bool
    "invalid_reason",      # "" if valid
    "latency_class",
    "price_source",
    "official_value",      # official XU030 at the SAME market_ts (NaN if none)
    "official_source",
    "diff_pts",            # calc - official
    "diff_pct",            # (calc - official) / official * 100
]


# --------------------------------------------------------------------------
# VIOP contracts
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class FuturesContract:
    code: str              # e.g. "F_XU0301226"  (Dec 2026)
    underlying: str        # "XU030"
    expiry: date           # last business day of the expiry month
    multiplier: float      # TL per index point (check current VIOP spec)
    tick_size: float
