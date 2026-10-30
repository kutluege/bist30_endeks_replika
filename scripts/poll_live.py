"""Live poller: Yahoo 1m bars (BIST 30 universe + XU030) and VİOP XU030 futures quotes (foreks.com).

* every ``--yahoo-every`` s (default 60): one batched yfinance request for today's 1m bars of the
  whole universe -> store.append_bars (upsert; last partial bar gets updated), delay per symbol
  -> delay_log.
* every ``--viop-every`` s (default 20): detail pages of each XU030 futures contract on foreks.com
  (last/bid/ask/volume/dateModified) -> store.append_quotes (source="foreks"); the list page
  (all contracts, last price + validFrom) every ``--list-every`` s (source="foreks_list").
* writes market_ts AND received_ts; logs to logs/poller.log; PID -> data/state/poller.pid.
* exits automatically at ``--until`` Istanbul time (default 18:35 so the ~15-min-delayed prints of
  the equity closing auction (18:10) and VİOP close (18:15) are still captured).

Run detached (PowerShell):
  Start-Process -WindowStyle Hidden -FilePath .venv\\Scripts\\python.exe -ArgumentList scripts\\poll_live.py
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
warnings.filterwarnings("ignore")

from bist30.sources import store  # noqa: E402
from bist30.sources.foreks import ForeksSource  # noqa: E402
from bist30.sources.yahoo import YahooSource, measure_delay  # noqa: E402

IST = ZoneInfo("Europe/Istanbul")

# BIST 30 candidates (2025-2026 compositions) + liquid BIST 50 spares. Unknown/renamed tickers
# simply return no data from Yahoo. Union with data/metadata/constituents.csv if present.
HARDCODED_UNIVERSE = [
    "AKBNK", "AEFES", "AGHOL", "ALARK", "ARCLK", "ASELS", "ASTOR", "BIMAS", "BRSAN", "BTCIM",
    "CCOLA", "CIMSA", "CWENE", "DOAS", "DOHOL", "DSTKF", "EKGYO", "ENERY", "ENJSA", "ENKAI",
    "EREGL", "FROTO", "GARAN", "GRSEL", "GUBRF", "HALKB", "HEKTS", "ISCTR", "ISMEN", "KCHOL",
    "KONTR", "KOZAA", "KOZAL", "KRDMD", "KTLEV", "MAVI", "MGROS", "MPARK", "OTKAR", "OYAKC",
    "PASEU", "PETKM", "PGSUS", "RALYH", "SAHOL", "SASA", "SISE", "SOKM", "TABGD", "TAVHL",
    "TCELL", "THYAO", "TOASO", "TRALT", "TRENJ", "TRMET", "TSKB", "TTKOM", "TTRAK", "TUPRS",
    "ULKER", "VAKBN", "VESTL", "YKBNK", "ZOREN", "GESAN", "EUPWR", "SMRTG", "ODAS", "AKSEN",
]
INDEXES = ["XU030", "XU100"]


def load_universe() -> list[str]:
    syms = set(HARDCODED_UNIVERSE)
    p = ROOT / "data" / "metadata" / "constituents.csv"
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    s = (r.get("symbol") or "").strip().upper()
                    if s:
                        syms.add(s)
        except Exception as e:  # pragma: no cover
            logging.warning("cannot read constituents.csv: %s", e)
    return sorted(syms) + INDEXES


def setup_logging() -> logging.Logger:
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(ROOT / "logs" / "poller.log", encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)])
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger("poller")


def save_raw(name: str, text: str) -> None:
    d = ROOT / "data" / "raw" / "live"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
    (d / f"{stamp}_{name}").write_text(text, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="18:35", help="Istanbul HH:MM to exit")
    ap.add_argument("--yahoo-every", type=float, default=60)
    ap.add_argument("--viop-every", type=float, default=20)
    ap.add_argument("--list-every", type=float, default=60)
    ap.add_argument("--raw-every", type=float, default=1800, help="save raw sample every N s")
    ap.add_argument("--no-yahoo", action="store_true")
    ap.add_argument("--no-viop", action="store_true")
    a = ap.parse_args(argv)

    log = setup_logging()
    pid_file = ROOT / "data" / "state" / "poller.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    hh, mm = map(int, a.until.split(":"))
    stop_at = datetime.now(IST).replace(hour=hh, minute=mm, second=0, microsecond=0)
    universe = load_universe()
    log.info("poller start pid=%s until=%s universe=%d symbols", os.getpid(), stop_at.isoformat(), len(universe))

    yahoo = YahooSource()
    foreks = ForeksSource(min_interval_s=3.0)
    next_y = next_v = next_l = next_raw = 0.0
    n_cycle = 0
    # Foreks serves pages from several SSR cache workers -> an older snapshot can come back after a
    # newer one. Only store snapshots whose market_ts is strictly newer than the last stored one.
    last_mts: dict[tuple[str, str], object] = {}
    try:
        with store.connect() as con:
            for src, sym, mx in con.execute("SELECT source, symbol, MAX(market_ts) FROM quotes GROUP BY 1, 2"):
                last_mts[(src, sym)] = store.to_utc_ts(mx).to_pydatetime()
    except Exception as e:
        log.warning("cannot seed last market_ts: %s", e)

    def fresh(q) -> bool:
        k = (q.source, q.symbol)
        if k in last_mts and q.market_ts <= last_mts[k]:
            return False
        last_mts[k] = q.market_ts
        return True
    try:
        while datetime.now(IST) < stop_at:
            now = time.monotonic()
            save_raw_now = now >= next_raw
            if save_raw_now:
                next_raw = now + a.raw_every
            # ---------------- Yahoo
            if not a.no_yahoo and now >= next_y:
                next_y = now + a.yahoo_every
                try:
                    df = yahoo.fetch_recent(universe, "1m", "1d")
                    n = store.append_bars(df)
                    dl = measure_delay(df)
                    store.append_delays([(r.received_ts, "yahoo", r.symbol, r.latest_market_ts, r.delay_s)
                                         for r in dl.itertuples()])
                    x = dl[dl.symbol == "XU030"]
                    log.info("yahoo: %d rows, %d symbols, %d upserted; delay median=%.0fs XU030=%s",
                             len(df), df["symbol"].nunique() if len(df) else 0, n,
                             dl["delay_s"].median() if len(dl) else float("nan"),
                             f"{x.delay_s.iloc[0]:.0f}s" if len(x) else "n/a")
                    if save_raw_now and len(df):
                        save_raw("yahoo_1m.csv", df.to_csv(index=False))
                except Exception as e:
                    log.exception("yahoo poll failed: %s", e)
            # ---------------- VİOP list page
            if not a.no_viop and now >= next_l:
                next_l = now + a.list_every
                try:
                    qs = foreks.list_quotes()
                    n = store.append_quotes([q for q in qs if fresh(q)])
                    log.info("foreks_list: %s (new %d)", ", ".join(f"{q.symbol}={q.price}@{q.market_ts:%H:%M:%S}" for q in qs), n)
                    if save_raw_now:
                        from bist30.sources.foreks import LIST_URL
                        save_raw("foreks_list.html", foreks.last_html.get(LIST_URL, ""))
                except Exception as e:
                    log.exception("foreks list failed: %s", e)
            # ---------------- VİOP detail pages (bid/ask)
            if not a.no_viop and now >= next_v:
                next_v = now + a.viop_every
                try:
                    contracts = foreks.list_contracts(max_age_s=300)
                    for c in contracts:
                        q, extra = foreks.detail_quote(c["url"])
                        if q is None:
                            log.warning("foreks detail parse failed %s", c["url"])
                            continue
                        n = store.append_quotes([q], extra={q.symbol: extra}) if fresh(q) else 0
                        if n:
                            store.append_delays([(q.received_ts, "foreks", q.symbol, q.market_ts,
                                                  (q.received_ts - q.market_ts).total_seconds())])
                        if n_cycle % 3 == 0 or n:
                            log.info("foreks: %s last=%s bid=%s ask=%s vol=%s mts=%s delay=%.0fs new=%d",
                                     q.symbol, q.price, q.bid, q.ask, q.volume, f"{q.market_ts:%H:%M:%S}",
                                     (q.received_ts - q.market_ts).total_seconds(), n)
                        if save_raw_now:
                            save_raw(f"foreks_{q.symbol}.html", foreks.last_html.get(c["url"], ""))
                except Exception as e:
                    log.exception("foreks detail failed: %s", e)
                n_cycle += 1
            time.sleep(1.0)
    finally:
        log.info("poller exit; counts=%s", {k: v for k, v in store.counts().items() if not isinstance(v, list)})
        try:
            if pid_file.read_text().strip() == str(os.getpid()):
                pid_file.unlink()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
