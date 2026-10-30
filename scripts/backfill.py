"""Backfill the market store with the maximum free history.

* Yahoo 1m bars for the universe + XU030/XU100: as far back as Yahoo serves (~29 days, chunked
  in 7-day requests; ``--days-1m``). Falls back to 2m/5m/15m/1d automatically if 1m is empty.
* Yahoo 1d bars (``--days-1d``, default 400) for the same symbols.
* Borsa İstanbul official EOD (``--days-eod``, default 10 business days): equity bulletin closes,
  VİOP XU030 futures settlement, latest official index closes (XU030...), TLREF history.

Usage:  .venv\\Scripts\\python.exe scripts\\backfill.py [--days-1m 29] [--no-eod]
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
warnings.filterwarnings("ignore")

from bist30.sources import store  # noqa: E402
from bist30.sources.borsaistanbul import BorsaIstanbulSource  # noqa: E402
from bist30.sources.yahoo import YahooSource  # noqa: E402
from poll_live import load_universe  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-1m", type=int, default=29)
    ap.add_argument("--days-1d", type=int, default=400)
    ap.add_argument("--days-eod", type=int, default=14, help="calendar days of BIST bulletins")
    ap.add_argument("--no-eod", action="store_true")
    ap.add_argument("--no-yahoo", action="store_true")
    ap.add_argument("--symbols", nargs="*", help="override universe")
    a = ap.parse_args(argv)

    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(ROOT / "logs" / "backfill.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    log = logging.getLogger("backfill")
    syms = a.symbols or load_universe()
    now = datetime.now(timezone.utc)

    if not a.no_yahoo:
        y = YahooSource()
        for iv, days in (("1m", a.days_1m), ("1d", a.days_1d)):
            # chunk symbols to keep each request moderate
            total = 0
            for i in range(0, len(syms), 25):
                chunk = syms[i:i + 25]
                df = y.fetch_bars(chunk, now - timedelta(days=days), now, interval=iv)
                n = store.append_bars(df)
                total += n
                if len(df):
                    log.info("yahoo %s %s..%s: %d rows (%s..%s), upserted %d", iv, chunk[0], chunk[-1], len(df),
                             df.market_ts.min(), df.market_ts.max(), n)
                else:
                    log.warning("yahoo %s %s..%s: no data", iv, chunk[0], chunk[-1])
            log.info("yahoo %s total upserted %d", iv, total)

    if not a.no_eod:
        raw = ROOT / "data" / "raw" / "borsaistanbul"
        b = BorsaIstanbulSource(raw_dir=raw)
        eq = [s for s in syms if not s.startswith("X")]
        fut = []
        try:
            # discover XU030 futures from the latest bulletin available
            for k in range(0, 7):
                d = (now - timedelta(days=k)).date()
                try:
                    v = b.fetch_viop_bulletin(d)
                    fut = sorted(v[(v.underlying == "D_XU030D") & v.symbol.str.startswith("F_XU030")].symbol.unique())
                    break
                except Exception:
                    continue
        except Exception as e:
            log.warning("viop discovery failed: %s", e)
        df = b.fetch_bars(["XU030", "XU100"] + eq + fut, now - timedelta(days=a.days_eod), now, interval="1d")
        log.info("borsaistanbul EOD: %d rows, symbols=%d, futures=%s, upserted %d",
                 len(df), df.symbol.nunique() if len(df) else 0, fut, store.append_bars(df))
        try:
            m = b.fetch_membership()
            out = ROOT / "data" / "raw" / "borsaistanbul" / "xu030_membership_latest.csv"
            m[m.index_code == "XU030"].to_csv(out, index=False)
            log.info("XU030 membership (%s): %s", m.date.max(), ",".join(m[m.index_code == "XU030"].symbol))
        except Exception as e:
            log.warning("membership failed: %s", e)

    c = store.counts()
    for row in c["bars_by_source_interval"]:
        log.info("store bars: %s", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
