"""Build the Turkish HTML replication report end to end.

    .venv/Scripts/python.exe scripts/build_report_html.py
    .venv/Scripts/python.exe scripts/build_report_html.py --refresh-days 7     # top up from Yahoo first

Steps: Yahoo 1m/1d bars (local store, optional in-memory Yahoo top-up) ->
calibrate the divisor ONCE on the official close of --calibration-date ->
compute every later minute with compute_index_series (rule-5.1 events from
corporate_actions.csv) -> metrics -> data/results/backtest_index.csv +
reports/xu030_replikasyon.html.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bist30.models import INDEX_RESULT_COLUMNS, ISTANBUL_TZ  # noqa: E402
from bist30.engine import EXTRA_RESULT_COLUMNS  # noqa: E402
from bist30.engine.session import DEFAULT_HALF_DAYS  # noqa: E402
from bist30.report_html import build_html, compute_metrics, export_payload  # noqa: E402
from bist30.report_html.backtest import (load_bars, load_constituents_union,  # noqa: E402
                                         provisional_constituents_from_yahoo, run_backtest, trading_days)
from bist30.report_html.metrics import tr_num, tr_pct  # noqa: E402

log = logging.getLogger("build_report_html")


def _event_text(notes: list[str]) -> str:
    out = []
    for n in notes:
        m = re.match(r"(\w+) FDPO ([\d.]+)->([\d.]+)", n)
        if m:
            out.append(f"{m[1]} FDPO %{float(m[2]) * 100:.0f} → %{float(m[3]) * 100:.0f}")
            continue
        m = re.match(r"(\w+) cash dividend", n)
        if m:
            out.append(f"{m[1]} nakit temettü (fiyat endeksinde düzeltme yok, 5.2.a)")
            continue
        if "constituent list" in n and out:
            continue   # same change also visible in the constituents file
        out.append(n)
    return "; ".join(out)


def divisor_events(run) -> list[dict]:
    prev = run.divisor_state.divisor
    out = []
    for e in run.events:
        if "error" in e:
            out.append({"date": e["date"], "changed": False, "text": "bölen düzeltmesi yapılamadı: " + e["error"]})
            continue
        d = e.get("divisor")
        changed = d is not None and abs(d - prev) > 1e-6
        out.append({"date": e["date"], "changed": bool(changed), "divisor": d, "previous": prev,
                    "text": _event_text(e.get("notes", []))})
        prev = d if d is not None else prev
    return out


def metadata_counts(days) -> dict:
    """FDPO provenance per constituent: Borsa index-FDPO notice (high confidence) vs
    rule-based estimate from MKK weekly data (medium confidence)."""
    try:
        from bist30.metadata import constituents_version, load_constituents_frame
        f = load_constituents_frame(as_of=days[-1])
        src = f["ff_source"].astype(str)
        notice = src.str.contains("Borsa index FDPO", case=False)
        mkk_dates = src[~notice].str.extract(r"MKK weekly FDPO (\d{4}-\d{2}-\d{2})")[0].dropna()
        return {"n_ff_estimate": int((~notice).sum()), "n_ff_notice": int(notice.sum()),
                "ff_notice_symbols": sorted(f.loc[notice, "symbol"]),
                "ff_estimate_mkk_date": mkk_dates.mode().iloc[0] if len(mkk_dates) else None,
                "version": constituents_version()}
    except Exception as e:   # pragma: no cover - only when metadata is missing
        return {"error": str(e)}


def limitations(metrics: dict, meta_counts: dict, run, days_info: list[dict]) -> list[dict]:
    inv = metrics["invalid"]
    out = []
    if meta_counts.get("n_ff_estimate"):
        d = meta_counts.get("ff_estimate_mkk_date")
        when = f" ({date.fromisoformat(d):%d.%m.%Y} haftası)" if d else ""
        out.append({"title": "FDPO güveni",
                    "text": f"{meta_counts['n_ff_estimate']} hissede Borsa İstanbul'un endeks FDPO bildirimi bulunamadı. "
                            f"Bu hisselerin H değeri MKK haftalık oranından{when} kural 5.2.f eşiklerine göre tahmin edildi "
                            f"(orta güven): endekste kullanılan oran bu tahminden eşik altında (≤5 / ≤10 puan) farklı "
                            f"olabilir. Bildirimle doğrulanan hisseler: {', '.join(meta_counts['ff_notice_symbols'])}."})
    opl = inv["by_reason"].get("outside_price_limit", 0)
    if opl:
        top = [r for r in inv["by_symbol"] if "outside_price_limit" in r]
        who = ", ".join(f"{r['symbol']} ({r['outside_price_limit']} dk)" for r in top[:3])
        out.append({"title": "Kapanış müzayedesi eksik",
                    "text": f"{opl} dakika 'fiyat limiti dışı' sayıldı ({who}). Limit kontrolünün baz fiyatı resmî kapanış "
                            f"(müzayede) yerine son 1 dakikalık işlem fiyatı olduğundan bu işaretlerin bir kısmı yanlış "
                            f"alarm olabilir. Bu dakikalar metriklere girmedi."})
    out.append({"title": "Dakika çözünürlüğü",
                "text": "Resmî endeks saniyede bir hesaplanır (2.18); biz dakika sonu değerlerini karşılaştırıyoruz. "
                        "Hesaplanan ve resmî değer aynı dakika sonuna aittir, ama dakika içindeki son işlem anı hisseden "
                        "hisseye farklıdır."})
    partial = [d for d in days_info if d.get("partial")]
    for d in partial:
        out.append({"title": "Kısmi gün",
                    "text": f"{date.fromisoformat(d['date']):%d.%m.%Y} verisi {d['last'][11:16]}'e kadar (15 dk gecikmeli kaynak, veri çekim anı)."})
    out.append({"title": "Kalibrasyon verisi",
                "text": "Bölen, Yahoo günlük kapanış barlarıyla (resmî kapanış fiyatları) kalibre edildi. Yahoo bazı günlük "
                        "barlarda hacmi 0 gösteriyor (ör. SASA 01.10.2026); günlük kapanışta bu 'hacim bilinmiyor' kabul edildi."})
    late = metrics["bias"].get("late_mean_pct")
    k = metrics["bias"]["third_len_days"]
    if late is not None and abs(late) >= metrics["thresholds"]["bias_econ_pct"]:
        cc = [r["diff_pct"] for r in metrics["close_check"] if not r["calibration_day"] and r.get("diff_pct") is not None]
        close_late = sum(cc[-k:]) / len(cc[-k:]) if cc else None
        close_txt = (f" Aynı günlerde müzayede dahil kapanış farkı ortalaması {tr_pct(close_late, 3, True)}"
                     f"{'; yani fark kapanış müzayedesinden kaynaklanmıyor' if close_late is not None and close_late * late > 0 else ''}."
                     if close_late is not None else "")
        out.append({"title": "Dönem sonu kayması",
                    "text": f"Son {k} işlem gününde dakika bazında ortalama fark {tr_pct(late, 3, True)}.{close_txt} "
                            f"Bu, ağırlıklarda (H veya N) yakalayamadığımız bir değişikliğe işaret edebilir; doğrulanmadı. "
                            f"Seri resmî değere yeniden uydurulmadı."})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calibration-date", default="2026-10-01")
    ap.add_argument("--end", default=None, help="last date (inclusive, Istanbul); default: all data")
    ap.add_argument("--refresh-days", type=int, default=0, help="top up the last N days from Yahoo (in memory)")
    ap.add_argument("--status", choices=["auto", "final", "preliminary", "provisional"], default="auto")
    ap.add_argument("--out-html", default=str(ROOT / "reports" / "xu030_replikasyon.html"))
    ap.add_argument("--out-csv", default=str(ROOT / "data" / "results" / "backtest_index.csv"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover
        pass

    cal_date = date.fromisoformat(args.calibration_date)
    start = pd.Timestamp(cal_date).tz_localize(ISTANBUL_TZ).tz_convert("UTC")
    end = (pd.Timestamp(args.end).tz_localize(ISTANBUL_TZ) + pd.Timedelta(days=1)).tz_convert("UTC") if args.end else None

    # constituents: official file (Methodology agent). Without it the report would need the
    # provisional Yahoo-float fallback (backtest.provisional_constituents_from_yahoo, labelled GEÇİCİ).
    metadata_file = (ROOT / "data" / "metadata" / "constituents.csv").exists()
    if metadata_file:
        from bist30.metadata import load_constituents
        symbols = sorted({c.symbol for c in load_constituents(as_of=cal_date)})
    else:  # pragma: no cover
        from bist30.metadata import load_constituents_frame  # noqa: F401  (raises a clear error)
        raise SystemExit("data/metadata/constituents.csv missing")

    m1, d1, load_info = load_bars(symbols, start=start, end=end, refresh_days=args.refresh_days)
    days = [d for d in trading_days(m1) if d >= cal_date]
    if len(days) < 2:
        raise SystemExit(f"need data after the calibration day; got days {days}")
    if metadata_file:
        constituents, origin = load_constituents_union(days)
        status = "final"
        metadata_label = "Resmî kaynaklar: Borsa İstanbul bileşen listesi, MKK pay sayısı ve FDPO, Borsa FDPO bildirimleri"
    else:  # pragma: no cover
        constituents, origin = provisional_constituents_from_yahoo(symbols), "yahoo_info"
        status = "provisional"
        metadata_label = "GEÇİCİ: Yahoo sharesOutstanding / floatShares"
    if args.status != "auto":
        status = args.status
    from bist30.metadata import load_corporate_actions
    actions = load_corporate_actions()

    log.info("days %s..%s (%d), 1m rows %d", days[0], days[-1], len(days), len(m1))
    run = run_backtest(m1, d1, constituents, cal_date, actions=actions)
    res = run.results
    metrics = compute_metrics(res, cal_date, close_check=run.close_check)

    # CSV: every row incl. the calibration day, flagged
    out_csv = Path(args.out_csv); out_csv.parent.mkdir(parents=True, exist_ok=True)
    csv = res[[c for c in INDEX_RESULT_COLUMNS + EXTRA_RESULT_COLUMNS if c in res.columns]].copy()
    lt = pd.to_datetime(csv["market_ts"], utc=True).dt.tz_convert(ISTANBUL_TZ)
    csv.insert(1, "market_ts_istanbul", lt.map(lambda t: t.isoformat()))
    csv.insert(2, "out_of_sample", (lt.dt.date > cal_date).to_numpy())
    csv.to_csv(out_csv, index=False)

    counts = metadata_counts(days)
    events = divisor_events(run)
    half = set(DEFAULT_HALF_DAYS)
    days_info = [{"date": d["date"], "last": d["last_ts"],
                  "partial": (date.fromisoformat(d["date"]) not in half
                              and int(d["last_ts"][11:13]) * 60 + int(d["last_ts"][14:16]) < 17 * 60 + 55)}
                 for d in metrics["per_day"]]
    now = pd.Timestamp.now(tz=ISTANBUL_TZ)
    v = metrics["verdict"]
    meta = {
        "status": status,
        "status_note": ("Ağırlıklar Yahoo float verisinden; resmî kaynaklarla doğrulanmadı. Sayılar nihai değildir."
                        if status == "provisional" else
                        "Sayılar doğrulama sürecinde; nihai sürümde değişebilir." if status == "preliminary" else ""),
        "generated_at": now.isoformat(),
        "generator": "scripts/build_report_html.py · bist30.report_html",
        "description": f"Bağımsız hesaplanan BIST 30 ile resmî XU030'un {days[1]:%d.%m.%Y}–{days[-1]:%d.%m.%Y} dakika "
                       f"bazında karşılaştırması. {v['headline']}",
        "data_source": "Yahoo Finance (yfinance) 1 dakikalık barlar: 30 hisse ve XU030.IS (resmî endeks)",
        "latency": "15 dakika gecikmeli (latency_class = delayed_15m); geriye dönük test",
        "calibration_date": cal_date.isoformat(),
        "calibration_note": "Yahoo günlük kapanış barları (resmî kapanış fiyatları), aynı anda resmî XU030 kapanışı",
        "metadata_label": metadata_label,
        "metadata_origin": origin,
        "metadata_counts": counts,
        "divisor_events": events,
        "final_divisor": run.final_divisor_state.divisor,
        "load_info": load_info,
        "limitations": limitations(metrics, counts, run, days_info),
    }
    payload = export_payload(res, constituents, run.divisor_state, metrics, meta)
    out = build_html(payload, args.out_html)

    s = metrics["summary"]
    print(f"days {days[1]}..{days[-1]} | rows {metrics['n_minutes_oos']} | compared {metrics['n_compared']} | "
          f"valid {s['pct_valid']:.1f}% | MAE {s['mae_pts']:.2f} pts ({s['mean_abs_pct']:.4f}%) | "
          f"bias {s['bias_pts']:+.2f} pts ({metrics['bias']['mean_pct']:+.4f}%) t_days {metrics['bias']['t_days']:.2f} | "
          f"max {s['max_abs_pct']:.4f}% | within ±0.10% {metrics['within_warn_pct']:.1f}% | "
          f"drift {metrics['cross_day_drift']['slope_bp_per_day']:+.2f} bp/day (t {metrics['cross_day_drift']['t']:.1f})")
    print("verdict:", v["answer"], "-", v["headline"])
    print("status:", status, "| html:", out, f"({out.stat().st_size / 1e6:.2f} MB) | csv:", out_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
