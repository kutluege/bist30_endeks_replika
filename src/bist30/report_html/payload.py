"""export_payload: engine output -> JSON-serialisable dict for the HTML page.

Timestamps are Europe/Istanbul ISO-8601 strings; NaN -> None. The series is
columnar (one list per field) to keep the embedded JSON small.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd

from bist30.engine.session import DEFAULT_HALF_DAYS
from bist30.models import ISTANBUL_TZ, Constituent, DivisorState

from .content import DIFFERENCES, PHASE_LABELS, REASON_LABELS, RULE_STEPS, RULESET_FILE, RULESET_TITLE

PAYLOAD_VERSION = 1


def _num(x, nd: int | None = None):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, nd) if nd is not None else v


def _iso_local(ts) -> str | None:
    if ts is None or (isinstance(ts, float) and math.isnan(ts)):
        return None
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert(ISTANBUL_TZ).isoformat()


def _jsonable(o):
    """Recursively convert numpy / pandas / date objects; NaN -> None."""
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return _num(o)
    if isinstance(o, pd.Timestamp):
        return _iso_local(o)
    if isinstance(o, datetime):
        return _iso_local(o)
    if isinstance(o, date):
        return o.isoformat()
    return o


def _session_end(d: date, last_local: pd.Timestamp) -> str:
    half = d in set(DEFAULT_HALF_DAYS) if DEFAULT_HALF_DAYS else False
    hh, mm = (12, 40) if half else (18, 0)
    nominal = pd.Timestamp(datetime(d.year, d.month, d.day, hh, mm)).tz_localize(ISTANBUL_TZ)
    return max(nominal, last_local).isoformat()


def _series(df: pd.DataFrame) -> dict:
    lt = pd.to_datetime(df["market_ts"], utc=True).dt.tz_convert(ISTANBUL_TZ)
    col = lambda c: df[c] if c in df.columns else pd.Series([None] * len(df), index=df.index)  # noqa: E731
    return {
        "t": [t.isoformat() for t in lt],
        "calc": [_num(v, 2) for v in col("calc_value")],
        "unchecked": [_num(v, 2) for v in col("calc_value_unchecked")],
        "off": [_num(v, 2) for v in col("official_value")],
        "dpts": [_num(v, 2) for v in col("diff_pts")],
        "dpct": [_num(v, 4) for v in col("diff_pct")],
        "valid": [1 if bool(v) else 0 for v in col("valid")],
        "reason": ["" if (v is None or (isinstance(v, float) and math.isnan(v))) else str(v)
                   for v in col("invalid_reason")],
        "phase": [str(v) if v is not None else "" for v in col("session_phase")],
        "n_comp": [int(v) if pd.notna(v) else None for v in col("n_components")],
        "n_stale": [int(v) if pd.notna(v) else None for v in col("n_stale")],
        "max_stal": [_num(v, 0) for v in col("max_staleness_s")],
        "divisor": [_num(v, 8) for v in col("divisor")],
    }


def _constituent_rows(constituents: list[Constituent], divisor_state: DivisorState) -> tuple[list[dict], float]:
    cal = divisor_state.adjustments[0] if divisor_state.adjustments else {}
    prices = cal.get("prices", {})
    ref_day = pd.Timestamp(divisor_state.reference_market_ts).tz_convert(ISTANBUL_TZ).date()
    active = [c for c in constituents
              if (c.effective_from is None or c.effective_from <= ref_day)
              and (c.effective_to is None or c.effective_to >= ref_day)]
    terms = {c.symbol: (prices.get(c.symbol) or math.nan) * c.shares_outstanding * c.free_float_ratio
             * c.weight_factor for c in active}
    total = sum(v for v in terms.values() if math.isfinite(v))
    later = {}
    for c in constituents:
        if c not in active:
            later.setdefault(c.symbol, []).append(c)
    rows = []
    for c in sorted(active, key=lambda c: -terms[c.symbol] if math.isfinite(terms[c.symbol]) else 0):
        term = terms[c.symbol]
        rows.append({
            "symbol": c.symbol, "name": c.name, "N": _num(c.shares_outstanding), "H": _num(c.free_float_ratio),
            "K": _num(c.weight_factor), "price_cal": _num(prices.get(c.symbol)),
            "mcap_term": _num(term), "weight_pct": _num(term / total * 100) if total else None,
            "changes": [{"from": ch.effective_from.isoformat() if ch.effective_from else None,
                         "N": _num(ch.shares_outstanding), "H": _num(ch.free_float_ratio),
                         "K": _num(ch.weight_factor)} for ch in later.get(c.symbol, [])],
        })
    return rows, total


def export_payload(index_results: pd.DataFrame, constituents: list[Constituent], divisor_state: DivisorState,
                   metrics: dict, meta: dict) -> dict:
    """Build the JSON payload for ``build_html``.

    ``index_results``: engine output (INDEX_RESULT_COLUMNS [+ EXTRA]); rows on or before
    ``meta['calibration_date']`` are dropped (only out-of-sample minutes are animated).
    ``constituents``: all constituent rows used (period changes allowed).
    ``divisor_state``: the CALIBRATION state (adjustments[0] holds prices at calibration).
    ``metrics``: ``compute_metrics(...)`` output. ``meta``: free-form run metadata;
    keys used by the page: status, generated_at, data_source, calibration_date,
    divisor_events, final_divisor, notes, metadata_info.
    """
    df = index_results.copy()
    cal_date = meta.get("calibration_date")
    if cal_date is not None:
        cal_date = pd.Timestamp(cal_date).date()
        lt = pd.to_datetime(df["market_ts"], utc=True).dt.tz_convert(ISTANBUL_TZ)
        df = df[(lt.dt.date > cal_date).to_numpy()]
    df = df.sort_values("market_ts").reset_index(drop=True)
    lt = pd.to_datetime(df["market_ts"], utc=True).dt.tz_convert(ISTANBUL_TZ)

    per_day = {d["date"]: d for d in metrics.get("per_day", [])}
    days = []
    for d, idx in df.groupby(lt.dt.date.to_numpy()).indices.items():
        first, last = int(idx.min()), int(idx.max())
        last_local = lt.iloc[last]
        end = _session_end(d, last_local)
        half = bool(DEFAULT_HALF_DAYS and d in set(DEFAULT_HALF_DAYS))
        partial = (not half) and last_local.hour * 60 + last_local.minute < 17 * 60 + 55
        days.append({"date": d.isoformat(), "start": first, "end": last + 1,
                     "domain_start": lt.iloc[first].floor("5min").isoformat(), "domain_end": end,
                     "half_day": half, "partial": bool(partial), "stats": per_day.get(d.isoformat())})

    cons_rows, total = _constituent_rows(constituents, divisor_state)
    cal = divisor_state.adjustments[0] if divisor_state.adjustments else {}
    payload = {
        "version": PAYLOAD_VERSION,
        "meta": _jsonable(meta),
        "metrics": _jsonable(metrics),
        "divisor": _jsonable({
            "value": divisor_state.divisor,
            "method": divisor_state.method,
            "reference_ts": _iso_local(divisor_state.reference_market_ts),
            "reference_official_value": divisor_state.reference_official_value,
            "reference_raw_mcap": divisor_state.reference_raw_mcap,
            "official_source": divisor_state.official_source,
            "constituents_version": divisor_state.constituents_version,
            "calibration_max_price_staleness_s": cal.get("max_price_staleness_s"),
            "raw_mcap_check": total,
        }),
        "constituents": _jsonable(cons_rows),
        "days": _jsonable(days),
        "series": _series(df),
        "content": {
            "ruleset_title": RULESET_TITLE, "ruleset_file": RULESET_FILE,
            "steps": RULE_STEPS, "differences": DIFFERENCES,
            "reason_labels": REASON_LABELS, "phase_labels": PHASE_LABELS,
        },
    }
    json.dumps(payload, allow_nan=False)   # fail early if anything is not serialisable
    return payload
