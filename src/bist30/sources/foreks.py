"""Foreks.com (ForInvest) public web pages -> VİOP BIST 30 futures quotes.

Only public HTML pages are used (robots.txt allows them; ``/api/`` is disallowed and NOT
used). Prices are read from the schema.org JSON-LD blocks the site embeds for search
engines:

* list page ``/borsa/vadeli-islemler/`` -> every XU030 contract: last price, daily change,
  ``validFrom`` timestamp, detail-page URL;
* detail page ``/sembol-detay/VXU030n/...`` -> last, open, high, low, previous close,
  **bid (Alış)**, **ask (Satış)**, volume, ``dateModified``.

No depth (order book) is published without login -> no OrderBookSnapshot.

Measured 2026-10-30: ``dateModified``/``validFrom`` lag wall-clock by ~15 min ->
``LatencyClass.DELAYED_15M``. Canonical symbol: ``F_XU030MMYY`` from the contract name
"XU030 (31 Ara 26) Vadeli" -> ``F_XU0301226``.  Foreks' own codes (FXU030-1/-2/-3) are
rolling (``-1`` = nearest expiry) and must not be used as identifiers.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests

from bist30.models import InstrumentType, LatencyClass, Quote

log = logging.getLogger(__name__)

BASE = "https://www.foreks.com"
LIST_URL = BASE + "/borsa/vadeli-islemler/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36 bist30-research")
TR_MONTHS = {"oca": 1, "şub": 2, "sub": 2, "mar": 3, "nis": 4, "may": 5, "haz": 6, "tem": 7,
             "ağu": 8, "agu": 8, "eyl": 9, "eki": 10, "kas": 11, "ara": 12}
_NAME_RE = re.compile(r"^\s*(?P<und>[A-Z0-9]+)\s*\((?P<d>\d{1,2})\s+(?P<m>\S+)\s+(?P<y>\d{2})\)\s*Vadeli", re.I)
_LDJSON_RE = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def canonical_from_name(name: str) -> Optional[tuple[str, str]]:
    """'XU030 (31 Ara 26) Vadeli' -> ('F_XU0301226', '2026-12-31')."""
    m = _NAME_RE.match(name or "")
    if not m:
        return None
    mon = TR_MONTHS.get(m.group("m").lower()[:3])
    if not mon:
        return None
    yy = int(m.group("y"))
    code = f"F_{m.group('und').upper()}{mon:02d}{yy:02d}"
    expiry = f"20{yy:02d}-{mon:02d}-{int(m.group('d')):02d}"
    return code, expiry


def _ldjson_blocks(html: str) -> list:
    out = []
    for m in _LDJSON_RE.finditer(html):
        try:
            out.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    return out


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def parse_list_page(html: str, underlying: str = "XU030") -> list[dict]:
    """Return [{foreks_code, name, symbol, expiry, price, change, change_pct, market_ts, url}]."""
    rows, seen = [], set()
    for blk in _ldjson_blocks(html):
        for d in _walk(blk):
            if d.get("@type") != "ListItem" or not isinstance(d.get("item"), dict):
                continue
            item = d["item"]
            name = item.get("name", "")
            if not name.upper().startswith(underlying + " "):
                continue
            can = canonical_from_name(name)
            if not can or can[0] in seen:
                continue
            offers = item.get("offers") or {}
            spec = offers.get("priceSpecification") or {}
            props = {p.get("name"): p.get("value") for p in offers.get("additionalProperty", []) if isinstance(p, dict)}
            vf = spec.get("validFrom")
            seen.add(can[0])
            rows.append({
                "foreks_code": d.get("name"), "name": name, "symbol": can[0], "expiry": can[1],
                "price": offers.get("price", spec.get("price")),
                "change": props.get("Günlük Değişim"), "change_pct": props.get("Günlük Değişim Yüzdesi"),
                "market_ts": pd.Timestamp(vf).tz_convert("UTC") if vf else None,
                "url": item.get("url") or d.get("url"),
            })
    return sorted(rows, key=lambda r: r["expiry"])


_DETAIL_MAP = {"Son Fiyat": "price", "Açılış": "open", "Günlük En Yüksek": "high",
               "Günlük En Düşük": "low", "Önceki Kapanış": "prev_close", "Günlük Değişim": "change",
               "Günlük Değişim Yüzdesi": "change_pct", "Alış": "bid", "Satış": "ask", "Hacim": "volume",
               "Uzlaşma Fiyatı": "settlement", "Önceki Uzlaşma": "prev_settlement"}


def parse_detail_page(html: str) -> Optional[dict]:
    """Return dict(name, symbol, expiry, price, open, high, low, prev_close, bid, ask, volume, market_ts)."""
    name = None
    for blk in _ldjson_blocks(html):
        for d in _walk(blk):
            t = d.get("@type")
            if t == "FinancialProduct" and canonical_from_name(d.get("name", "")):
                name = name or d["name"]
            if t == "Dataset" and isinstance(d.get("variableMeasured"), list):
                vals = {}
                for p in d["variableMeasured"]:
                    if isinstance(p, dict) and p.get("name") in _DETAIL_MAP:
                        vals[_DETAIL_MAP[p["name"]]] = p.get("value")
                if "price" not in vals:
                    continue
                dname = name or re.sub(r"\s*\(F[^)]*\)\s*fiyat verileri\s*$", "", d.get("name", ""))
                can = canonical_from_name(dname)
                ts = d.get("dateModified")
                vals.update({"name": dname, "symbol": can[0] if can else None,
                             "expiry": can[1] if can else None,
                             "market_ts": pd.Timestamp(ts).tz_convert("UTC") if ts else None})
                return vals
    return None


class ForeksSource:
    """VİOP BIST 30 futures quotes (last/bid/ask) from public foreks.com pages."""

    name = "foreks"
    latency_class = LatencyClass.DELAYED_15M

    def __init__(self, min_interval_s: float = 3.0, timeout: int = 20):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"})
        self.min_interval_s = min_interval_s
        self.timeout = timeout
        self._last_req = 0.0
        self._contracts: list[dict] = []
        self._contracts_ts = 0.0
        self.last_html: dict[str, str] = {}

    def _get(self, url: str) -> str:
        wait = self.min_interval_s - (time.monotonic() - self._last_req)
        if wait > 0:
            time.sleep(wait)
        self._last_req = time.monotonic()
        r = self.s.get(url, timeout=self.timeout)
        r.raise_for_status()
        r.encoding = "utf-8"
        self.last_html[url] = r.text
        return r.text

    def list_contracts(self, max_age_s: float = 300) -> list[dict]:
        if not self._contracts or time.monotonic() - self._contracts_ts > max_age_s:
            html = self._get(LIST_URL)
            rec = datetime.now(timezone.utc)
            rows = parse_list_page(html)
            for r in rows:
                r["received_ts"] = rec
            self._contracts = rows
            self._contracts_ts = time.monotonic()
        return self._contracts

    def list_quotes(self) -> list[Quote]:
        """Last price for every XU030 contract from the list page (1 request, no bid/ask)."""
        self._contracts_ts = 0
        out = []
        for r in self.list_contracts():
            if r["price"] is None or r["market_ts"] is None:
                continue
            out.append(Quote(symbol=r["symbol"], instrument_type=InstrumentType.FUTURE,
                             price=float(r["price"]), market_ts=r["market_ts"].to_pydatetime(),
                             received_ts=r["received_ts"], source=self.name + "_list",
                             latency_class=self.latency_class))
        return out

    def detail_quote(self, url: str) -> tuple[Optional[Quote], Optional[dict]]:
        html = self._get(url)
        rec = datetime.now(timezone.utc)
        d = parse_detail_page(html)
        if not d or d.get("symbol") is None or d.get("market_ts") is None or d.get("price") is None:
            return None, d
        q = Quote(symbol=d["symbol"], instrument_type=InstrumentType.FUTURE, price=float(d["price"]),
                  market_ts=d["market_ts"].to_pydatetime(), received_ts=rec, source=self.name,
                  latency_class=self.latency_class,
                  volume=None if d.get("volume") is None else float(d["volume"]),
                  bid=None if d.get("bid") is None else float(d["bid"]),
                  ask=None if d.get("ask") is None else float(d["ask"]))
        extra = {k: d.get(k) for k in ("open", "high", "low", "prev_close", "change", "change_pct",
                                        "settlement", "prev_settlement", "expiry", "name")}
        return q, extra

    def fetch_quotes(self, symbols: Optional[list[str]] = None) -> list[Quote]:
        """Detail-page quotes (with bid/ask) for the requested canonical futures symbols."""
        out = []
        for c in self.list_contracts():
            if symbols and c["symbol"] not in symbols:
                continue
            try:
                q, _ = self.detail_quote(c["url"])
            except Exception as e:
                log.warning("foreks detail %s failed: %s", c["url"], e)
                continue
            if q:
                out.append(q)
        return out

    def fetch_bars(self, symbols, start, end, interval="1m"):
        raise NotImplementedError("foreks: only snapshot quotes are available (see bars_from_quotes)")


def bars_from_quotes(quotes: pd.DataFrame, interval: str = "1min", source: str = "foreks",
                     latency_class: LatencyClass = LatencyClass.DELAYED_15M) -> pd.DataFrame:
    """Resample polled quote snapshots (store.read_quotes) into BAR_COLUMNS bars.

    Bar = [start, start+interval); OHLC from the distinct last prices observed with
    market_ts inside the bar; market_ts = bar end. Volume = diff of cumulative daily volume.
    These are snapshot bars (sampling of a delayed feed), not exchange-official bars.
    """
    from bist30.models import BAR_COLUMNS, empty_bars
    if quotes is None or len(quotes) == 0:
        return empty_bars()
    q = quotes.drop_duplicates(["symbol", "market_ts"], keep="first").copy()
    frames = []
    for sym, g in q.groupby("symbol"):
        g = g.set_index("market_ts").sort_index()
        o = g["price"].resample(interval, label="left", closed="left").ohlc().dropna()
        v = g["volume"].resample(interval, label="left", closed="left").last().reindex(o.index)
        rec = g["received_ts"].resample(interval, label="left", closed="left").max().reindex(o.index)
        start = o.index
        frames.append(pd.DataFrame({
            "symbol": sym, "instrument_type": InstrumentType.FUTURE.value,
            "bar_start": start, "market_ts": start + pd.Timedelta(interval),
            "open": o["open"].values, "high": o["high"].values, "low": o["low"].values,
            "close": o["close"].values, "volume": v.diff().values, "source": source,
            "latency_class": latency_class.value, "interval": interval.replace("min", "m"),
            "received_ts": rec.values}))
    out = pd.concat(frames, ignore_index=True)
    return out[BAR_COLUMNS]
