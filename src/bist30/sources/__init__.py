"""Market-data adapters (owner: Market Data Agent).

    from bist30.sources import get_source
    yahoo = get_source("yahoo")              # equities + official XU030 (XU030.IS), 15-min delayed
    bist = get_source("borsaistanbul")       # official EOD files (index close, bulletins, VİOP settlement)
    viop = get_source("foreks")              # VİOP XU030 futures last/bid/ask (15-min delayed snapshots)

Canonical symbols: equity "AKBNK", index "XU030", future "F_XU0301226".
Storage: ``bist30.sources.store`` (SQLite, data/db/market.sqlite).
Short rate: ``bist30.sources.rates.get_short_rate()``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from bist30.models import LatencyClass, Quote


@runtime_checkable
class Source(Protocol):
    name: str
    latency_class: LatencyClass

    def fetch_bars(self, symbols: list[str], start: datetime, end: datetime,
                   interval: str = "1m") -> pd.DataFrame: ...

    def fetch_quotes(self, symbols: list[str]) -> list[Quote]: ...


_REGISTRY = {
    "yahoo": ("bist30.sources.yahoo", "YahooSource"),
    "borsaistanbul": ("bist30.sources.borsaistanbul", "BorsaIstanbulSource"),
    "bist": ("bist30.sources.borsaistanbul", "BorsaIstanbulSource"),
    "foreks": ("bist30.sources.foreks", "ForeksSource"),
}


def available_sources() -> list[str]:
    return sorted(_REGISTRY)


def get_source(name: str, **kwargs) -> Source:
    import importlib
    try:
        mod, cls = _REGISTRY[name.lower()]
    except KeyError:
        raise KeyError(f"unknown source {name!r}; available: {available_sources()}") from None
    return getattr(importlib.import_module(mod), cls)(**kwargs)


__all__ = ["Source", "get_source", "available_sources"]
