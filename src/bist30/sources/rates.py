"""TL short rate for futures carry (cost-of-carry) calculations.

Priority (``get_short_rate``):
  1. TLREF daily  - https://www.borsaistanbul.com/datum/tlreforani.csv   (Borsa İstanbul, free)
  2. TLREF history - https://www.borsaistanbul.com/datum/TLREFORANI_D.zip (UTF-16 CSV)
  3. TCMB one-week repo (policy) rate - HTML table on www.tcmb.gov.tr
  4. fallback constant: env ``BIST30_FALLBACK_RATE`` (decimal, e.g. 0.36) or ``DEFAULT_FALLBACK``.

All rates are returned as DECIMAL annual rates (35.7733 % -> 0.357733). TLREF is an overnight
simple rate on ACT/365 basis; the policy rate is a one-week repo rate. TCMB EVDS (full history
API) needs a personal API key -> not used (user action required if wanted).
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

TLREF_DAILY_URL = "https://www.borsaistanbul.com/datum/tlreforani.csv"
TLREF_HIST_URL = "https://www.borsaistanbul.com/datum/TLREFORANI_D.zip"
TCMB_REPO_URL = ("https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Temel+Faaliyetler/"
                 "Para+Politikasi/Merkez+Bankasi+Faiz+Oranlari/1+Hafta+Repo")
DEFAULT_FALLBACK = 0.36   # TCMB policy rate 36.00 % (decision of 2026-10-23), checked 2026-10-30
UA = {"User-Agent": "Mozilla/5.0 bist30-research"}


@dataclass(frozen=True)
class RateObservation:
    rate: float            # decimal annual rate
    as_of: date            # date the rate refers to
    source: str            # "tlref" | "tlref_hist" | "tcmb_policy" | "fallback"
    name: str
    received_ts: datetime


def parse_tlref_daily(text: str) -> pd.DataFrame:
    lines = [l for l in text.splitlines() if l.strip()]
    df = pd.read_csv(io.StringIO("\n".join([lines[1]] + lines[2:])), sep=";", dtype=str)
    return pd.DataFrame({"date": pd.to_datetime(df["DATE"], format="%d/%m/%Y").dt.date,
                         "rate_pct": pd.to_numeric(df["VALUE"])})


def parse_tlref_hist(raw: bytes) -> pd.DataFrame:
    for enc in ("utf-16", "utf-8-sig", "cp1254"):
        try:
            text = raw.decode(enc)
            if "TLREF" in text:
                break
        except UnicodeDecodeError:
            continue
    lines = [l for l in text.splitlines() if l.strip()]
    rows = []
    for l in lines[1:]:
        parts = l.split(";")
        if len(parts) < 6:
            continue
        try:
            d = datetime.strptime(parts[0].strip(), "%d/%m/%Y").date()
            v = float(parts[5].strip().replace(",", ""))
        except ValueError:
            continue
        rows.append((d, v))
    return pd.DataFrame(rows, columns=["date", "rate_pct"]).sort_values("date").reset_index(drop=True)


def parse_tcmb_repo(html: str) -> pd.DataFrame:
    t = pd.read_html(io.StringIO(html))[0]
    t = t.iloc[1:, [0, 2]] if str(t.iloc[0, 0]).startswith("Tarih") else t.iloc[:, [0, 2]]
    t.columns = ["date", "rate_pct"]
    t["date"] = pd.to_datetime(t["date"], format="%d.%m.%Y", errors="coerce").dt.date
    t["rate_pct"] = pd.to_numeric(t["rate_pct"].astype(str).str.replace(",", "."), errors="coerce")
    return t.dropna().sort_values("date").reset_index(drop=True)


def _get(url: str, timeout: int = 30) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=timeout)
    r.raise_for_status()
    return r


def tlref_history() -> pd.DataFrame:
    z = zipfile.ZipFile(io.BytesIO(_get(TLREF_HIST_URL).content))
    return parse_tlref_hist(z.read(z.namelist()[0]))


def fallback_rate() -> float:
    try:
        return float(os.environ.get("BIST30_FALLBACK_RATE", DEFAULT_FALLBACK))
    except ValueError:
        return DEFAULT_FALLBACK


def get_short_rate(as_of: Optional[date] = None, use_network: bool = True) -> RateObservation:
    """Latest TL short rate on or before ``as_of`` (default: latest available)."""
    now = datetime.now(timezone.utc)
    if use_network:
        if as_of is None:
            try:
                d = parse_tlref_daily(_get(TLREF_DAILY_URL).content.decode("utf-8-sig", "replace"))
                r = d.iloc[-1]
                return RateObservation(float(r.rate_pct) / 100, r.date, "tlref", "TLREF", now)
            except Exception as e:
                log.warning("TLREF daily failed: %s", e)
        try:
            h = tlref_history()
            if as_of is not None:
                h = h[h["date"] <= as_of]
            if len(h):
                r = h.iloc[-1]
                return RateObservation(float(r.rate_pct) / 100, r.date, "tlref_hist", "TLREF", now)
        except Exception as e:
            log.warning("TLREF history failed: %s", e)
        try:
            t = parse_tcmb_repo(_get(TCMB_REPO_URL).text)
            if as_of is not None:
                t = t[t["date"] <= as_of]
            r = t.iloc[-1]
            return RateObservation(float(r.rate_pct) / 100, r.date, "tcmb_policy", "TCMB 1 hafta repo", now)
        except Exception as e:
            log.warning("TCMB policy rate failed: %s", e)
    return RateObservation(fallback_rate(), as_of or now.date(), "fallback", "configured constant", now)
