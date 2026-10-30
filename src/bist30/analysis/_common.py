"""Shared helpers: feasibility status, latency-class guards, time handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from bist30.models import ISTANBUL_TZ, LatencyClass


class LatencyMismatchError(ValueError):
    """Raised when data with different latency classes would be compared."""


@dataclass
class Feasibility:
    """Outcome of an experiment. ``feasible=False`` carries an explicit reason and
    NO numbers - infeasible experiments never return fabricated results."""

    name: str
    feasible: bool
    reason: str = ""
    result: Any = None
    details: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        return "ok" if self.feasible else f"not feasible: {self.reason}"

    @classmethod
    def no(cls, name: str, reason: str, **details) -> "Feasibility":
        return cls(name=name, feasible=False, reason=reason, result=None, details=details)


def latency_classes(df: Optional[pd.DataFrame], col: str = "latency_class") -> set[str]:
    if df is None or len(df) == 0 or col not in df.columns:
        return set()
    vals = df[col].dropna().astype(str)
    return {LatencyClass(v).value if v in LatencyClass._value2member_map_ else v for v in vals.unique()}


def check_same_latency(a: Optional[pd.DataFrame], b: Optional[pd.DataFrame],
                       a_name: str = "a", b_name: str = "b") -> Optional[str]:
    """Return None if both frames carry exactly one, identical, known latency class;
    otherwise a human-readable reason."""
    la, lb = latency_classes(a), latency_classes(b)
    if not la:
        return f"{a_name}: latency_class missing"
    if not lb:
        return f"{b_name}: latency_class missing"
    if len(la) > 1:
        return f"{a_name}: mixed latency classes {sorted(la)}"
    if len(lb) > 1:
        return f"{b_name}: mixed latency classes {sorted(lb)}"
    (xa,), (xb,) = la, lb
    if LatencyClass.UNKNOWN.value in (xa, xb):
        return f"latency class unknown ({a_name}={xa}, {b_name}={xb}); cannot verify comparability"
    if xa != xb:
        return f"latency mismatch: {a_name}={xa} vs {b_name}={xb}"
    return None


def to_utc(s: pd.Series | pd.DatetimeIndex) -> pd.Series:
    s = pd.to_datetime(s)
    if isinstance(s, pd.DatetimeIndex):
        return s.tz_localize("UTC") if s.tz is None else s.tz_convert("UTC")
    return s.dt.tz_localize("UTC") if s.dt.tz is None else s.dt.tz_convert("UTC")


def istanbul_date(ts: pd.Series) -> pd.Series:
    return to_utc(ts).dt.tz_convert(ISTANBUL_TZ).dt.date


def interval_seconds(interval: str) -> float:
    """'1m' -> 60, '5m' -> 300, '1h' -> 3600, '1d' -> 86400, 'tick' -> 0."""
    if interval in (None, "") or (isinstance(interval, float) and np.isnan(interval)):
        return float("nan")
    s = str(interval).strip().lower()
    if s == "tick":
        return 0.0
    units = {"s": 1, "m": 60, "min": 60, "h": 3600, "d": 86400, "wk": 604800}
    for u in sorted(units, key=len, reverse=True):
        if s.endswith(u) and s[: -len(u)].replace(".", "").isdigit():
            return float(s[: -len(u)]) * units[u]
    return float("nan")


def series_from(obj, value_col: str = "close", ts_col: str = "market_ts") -> pd.Series:
    """Bars frame / index_result frame / Series -> float Series indexed by UTC market_ts."""
    if isinstance(obj, pd.Series):
        s = obj.astype(float)
        s.index = to_utc(pd.DatetimeIndex(s.index))
        return s.sort_index()
    df = obj
    s = pd.Series(df[value_col].astype(float).to_numpy(), index=pd.DatetimeIndex(to_utc(df[ts_col])))
    s = s[~s.index.duplicated(keep="last")]
    return s.sort_index()
