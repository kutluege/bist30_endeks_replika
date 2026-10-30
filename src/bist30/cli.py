"""Command line entry point (owner: orchestrator).

    bist30 collect           # backfill Yahoo 1m/1d + official BIST EOD into the store
    bist30 run               # calibrate once, EOD + 1m backtest, VIOP basis, report, figures
    bist30 html              # rebuild the animated HTML report
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date

from bist30.pipeline import ROOT


def _print_run(out: dict) -> None:
    s = out["intraday_summary"]
    eod = out["eod"].dropna(subset=["diff_pts"])
    print(f"\nDivisor (calibrated once): {out['calibration']['divisor']:.8f} "
          f"@ {out['calibration']['reference_market_ts']}")
    print(f"EOD   : {len(eod)} days  MAE={eod['diff_pts'].abs().mean():.2f} pts  "
          f"mean|%|={eod['diff_pct'].abs().mean():.4f}  max|%|={eod['diff_pct'].abs().max():.4f}")
    print("1m    : " + "  ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}"
                                 for k, v in s.items() if not isinstance(v, (dict, list))))
    print(f"lag   : best lag {out['lag'].attrs.get('best_lag')} min")
    for code, v in out["viop"].get("contracts", {}).items():
        print(f"VIOP {code}: {v.get('summary', v.get('error'))}")
    print(f"report: {ROOT / 'reports' / 'summary.md'}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bist30")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    r = sub.add_parser("run")
    r.add_argument("--calib-date", type=date.fromisoformat, default=date(2026, 10, 1))
    r.add_argument("--end-date", type=date.fromisoformat, default=None)
    r.add_argument("--staleness-s", type=float, default=300)
    r.add_argument("--no-plots", action="store_true")
    sub.add_parser("html")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if a.cmd == "collect":
        return subprocess.call([sys.executable, str(ROOT / "scripts" / "backfill.py")])
    if a.cmd == "html":
        return subprocess.call([sys.executable, str(ROOT / "scripts" / "build_report_html.py")])
    from bist30.pipeline import run_all
    _print_run(run_all(a.calib_date, a.end_date, a.staleness_s, make_plots=not a.no_plots))
    return 0


if __name__ == "__main__":
    sys.exit(main())
