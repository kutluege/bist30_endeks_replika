"""Load and validate BIST 30 constituent metadata and corporate actions.

Files (owned by the Index Methodology Agent):
  data/metadata/constituents.csv       -> CONSTITUENT_COLUMNS
  data/metadata/corporate_actions.csv  -> CORPORATE_ACTION_COLUMNS

A constituent row is active on ``as_of`` when
``effective_from <= as_of <= effective_to`` (blank ``effective_to`` = open ended).
The same symbol may appear in several rows (e.g. a free-float change inside the
period) as long as their validity windows do not overlap.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from bist30.models import (
    CONSTITUENT_COLUMNS,
    CORPORATE_ACTION_COLUMNS,
    ISTANBUL_TZ,
    Constituent,
)

PathLike = Union[str, Path]

PROJECT_ROOT = Path(__file__).resolve().parents[3]
METADATA_DIR = PROJECT_ROOT / "data" / "metadata"
DEFAULT_CONSTITUENTS_PATH = METADATA_DIR / "constituents.csv"
DEFAULT_CORPORATE_ACTIONS_PATH = METADATA_DIR / "corporate_actions.csv"

EXPECTED_COUNT = 30

ACTION_TYPES = {
    "bonus_issue", "rights_issue", "cash_dividend", "split", "ff_change",
    "inclusion", "exclusion", "merger", "spinoff", "share_conversion",
}


class MetadataError(ValueError):
    """Raised when a metadata file violates the contract."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _to_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        return date.fromisoformat(value)
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise MetadataError(f"cannot parse date: {value!r}")


def _today_istanbul() -> date:
    return pd.Timestamp.now(tz=ISTANBUL_TZ).date()


def _read_constituents_frame(path: PathLike) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise MetadataError(f"constituents file not found: {path}")
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    missing = [c for c in CONSTITUENT_COLUMNS if c not in df.columns]
    if missing:
        raise MetadataError(f"{path.name}: missing columns {missing}")
    df = df[CONSTITUENT_COLUMNS].copy()
    for c in ("symbol", "name", "yahoo_ticker"):
        df[c] = df[c].str.strip()
    try:
        df["shares_outstanding"] = df["shares_outstanding"].astype(float)
        df["free_float_ratio"] = df["free_float_ratio"].astype(float)
        df["weight_factor"] = df["weight_factor"].replace("", "1").astype(float)
    except ValueError as exc:  # pragma: no cover - message path
        raise MetadataError(f"{path.name}: non-numeric N/H/K value ({exc})") from exc
    df["effective_from"] = df["effective_from"].map(_to_date)
    df["effective_to"] = df["effective_to"].map(_to_date)
    return df


def _validate_rows(df: pd.DataFrame) -> None:
    """Row-level checks that hold for the whole file."""
    errors: list[str] = []
    for i, r in df.iterrows():
        tag = f"row {i + 2} ({r['symbol'] or '?'})"
        if not r["symbol"]:
            errors.append(f"{tag}: empty symbol")
        if not r["yahoo_ticker"]:
            errors.append(f"{tag}: empty yahoo_ticker")
        n, h, k = r["shares_outstanding"], r["free_float_ratio"], r["weight_factor"]
        if not (math.isfinite(n) and n > 0):
            errors.append(f"{tag}: shares_outstanding must be > 0 (got {n})")
        if not (math.isfinite(h) and 0 < h <= 1):
            errors.append(f"{tag}: free_float_ratio must be in (0, 1] (got {h})")
        if not (math.isfinite(k) and 0 < k <= 1):
            errors.append(f"{tag}: weight_factor must be in (0, 1] (got {k})")
        if r["effective_from"] is None:
            errors.append(f"{tag}: effective_from is required")
        elif r["effective_to"] is not None and r["effective_to"] < r["effective_from"]:
            errors.append(f"{tag}: effective_to before effective_from")
    # overlapping windows for the same symbol
    for sym, g in df.groupby("symbol"):
        g = g.sort_values("effective_from")
        prev_to = None
        for _, r in g.iterrows():
            if prev_to is not None and (prev_to == "open" or r["effective_from"] <= prev_to):
                errors.append(f"{sym}: overlapping effective windows")
                break
            prev_to = r["effective_to"] if r["effective_to"] is not None else "open"
    if errors:
        raise MetadataError("constituents validation failed:\n  " + "\n  ".join(errors))


def _active_mask(df: pd.DataFrame, as_of: date) -> pd.Series:
    start_ok = df["effective_from"].map(lambda d: d is not None and d <= as_of)
    end_ok = df["effective_to"].map(lambda d: d is None or d >= as_of)
    return start_ok & end_ok


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def load_constituents_frame(as_of: Optional[date] = None, path: Optional[PathLike] = None,
                            validate: bool = True) -> pd.DataFrame:
    """Active constituent rows (all CONSTITUENT_COLUMNS, incl. sources/notes) on ``as_of``."""
    path = Path(path) if path is not None else DEFAULT_CONSTITUENTS_PATH
    as_of = _to_date(as_of) if as_of is not None else _today_istanbul()
    df = _read_constituents_frame(path)
    if validate:
        _validate_rows(df)
    active = df[_active_mask(df, as_of)].copy()
    if validate:
        dups = active["symbol"][active["symbol"].duplicated()].tolist()
        if dups:
            raise MetadataError(f"duplicate active symbols on {as_of}: {dups}")
        if len(active) != EXPECTED_COUNT:
            raise MetadataError(
                f"expected exactly {EXPECTED_COUNT} active constituents on {as_of}, "
                f"found {len(active)} in {path.name}")
    return active.reset_index(drop=True)


def load_constituents(as_of: Optional[date] = None, path: Optional[PathLike] = None) -> list[Constituent]:
    """Return the 30 XU030 constituents active on ``as_of`` (default: today, Istanbul).

    Raises ``MetadataError`` if the file breaks the contract (not exactly 30 active
    symbols, duplicates, H outside (0, 1], N <= 0, ...).
    """
    active = load_constituents_frame(as_of=as_of, path=path, validate=True)
    return [
        Constituent(
            symbol=r.symbol,
            name=r.name,
            yahoo_ticker=r.yahoo_ticker,
            shares_outstanding=float(r.shares_outstanding),
            free_float_ratio=float(r.free_float_ratio),
            weight_factor=float(r.weight_factor),
            effective_from=r.effective_from,
            effective_to=r.effective_to,
        )
        for r in active.sort_values("symbol").itertuples(index=False)
    ]


def load_corporate_actions(path: Optional[PathLike] = None) -> pd.DataFrame:
    """Corporate actions as a DataFrame with exactly CORPORATE_ACTION_COLUMNS.

    Dates are ``datetime.date`` (or None), numeric columns float (NaN if blank),
    ``affects_price_index`` bool.
    """
    path = Path(path) if path is not None else DEFAULT_CORPORATE_ACTIONS_PATH
    if not path.exists():
        raise MetadataError(f"corporate actions file not found: {path}")
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    missing = [c for c in CORPORATE_ACTION_COLUMNS if c not in df.columns]
    if missing:
        raise MetadataError(f"{path.name}: missing columns {missing}")
    df = df[CORPORATE_ACTION_COLUMNS].copy()
    bad = sorted(set(df["action_type"]) - ACTION_TYPES)
    if bad:
        raise MetadataError(f"{path.name}: unknown action_type {bad}")
    for c in ("announcement_date", "effective_date"):
        df[c] = df[c].map(_to_date)
    for c in ("ratio", "cash_amount", "new_shares_outstanding", "new_free_float_ratio"):
        df[c] = pd.to_numeric(df[c].replace("", None), errors="raise").astype(float)
    truthy = {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}
    vals = df["affects_price_index"].str.strip().str.lower()
    if not vals.isin(truthy.keys()).all():
        raise MetadataError(f"{path.name}: affects_price_index must be true/false")
    df["affects_price_index"] = vals.map(truthy).astype(bool)
    ff = df["new_free_float_ratio"].dropna()
    if ((ff <= 0) | (ff > 1)).any():
        raise MetadataError(f"{path.name}: new_free_float_ratio outside (0, 1]")
    return df.sort_values(["effective_date", "symbol"], na_position="last").reset_index(drop=True)


def constituents_version(path: Optional[PathLike] = None) -> str:
    """Short content hash of the constituents file (stable across line endings)."""
    path = Path(path) if path is not None else DEFAULT_CONSTITUENTS_PATH
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()[:16]
