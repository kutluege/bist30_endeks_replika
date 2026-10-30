"""Tests for bist30.report_html (payload export, HTML build, rule-set citations)."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bist30.engine import calibrate_divisor, compute_index_series
from bist30.models import INDEX_RESULT_COLUMNS, Constituent
from bist30.report_html import build_html, compute_metrics, export_payload, extract_payload
from bist30.report_html.content import RULE_STEPS, reason_key

ROOT = Path(__file__).resolve().parents[1]
RULESET = ROOT / "docs" / "kural_seti.txt"


# ------------------------------------------------------------------ synthetic run
def _bars(days, symbols, rng):
    rows = []
    for d in days:
        for k in range(1, 121):     # 10:01 .. 12:00 Istanbul (UTC+3)
            ts = pd.Timestamp(datetime(d.year, d.month, d.day, 7, 0, tzinfo=timezone.utc)) + pd.Timedelta(minutes=k)
            for j, (s, base) in enumerate(symbols.items()):
                px = base * (1 + 0.001 * np.sin(k / 9 + 2 * j) + 0.0002 * rng.standard_normal())
                rows.append({"symbol": s, "instrument_type": "equity", "bar_start": ts - pd.Timedelta(minutes=1),
                             "market_ts": ts, "open": px, "high": px, "low": px, "close": px, "volume": 1000.0,
                             "source": "test", "latency_class": "delayed_15m", "interval": "1m", "received_ts": ts})
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def run():
    rng = np.random.default_rng(7)
    syms = {"AAA": 50.0, "BBB": 120.0, "CCC": 8.0}
    cons = [Constituent(s, s + " A.Ş.", s + ".IS", 1e9 * (i + 1), 0.4 + 0.1 * i, 1.0) for i, s in enumerate(syms)]
    days = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)]
    bars = _bars(days, syms, rng)
    # official = true index with a small bias so metrics are non-trivial
    piv = bars.pivot(index="market_ts", columns="symbol", values="close")
    mc = sum(piv[c.symbol] * c.shares_outstanding * c.free_float_ratio for c in cons)
    # official 0.03% below the true index AFTER the calibration day (a weights error appearing later)
    after = pd.Series(mc.index.tz_convert("Europe/Istanbul").date > days[0], index=mc.index)
    off_val = mc / 1e7 * np.where(after, 1 - 0.0003, 1.0)
    off = pd.DataFrame({"symbol": "XU030", "instrument_type": "index", "market_ts": off_val.index,
                        "bar_start": off_val.index - pd.Timedelta(minutes=1), "open": off_val.values,
                        "high": off_val.values, "low": off_val.values, "close": off_val.values, "volume": 0.0,
                        "source": "test", "latency_class": "delayed_15m", "interval": "1m",
                        "received_ts": off_val.index})
    ref = off_val.index[off_val.index.tz_convert("Europe/Istanbul").date == days[0]][-1]
    state = calibrate_divisor(bars, cons, off, ref)
    res = compute_index_series(pd.concat([bars, off], ignore_index=True), cons, state)
    # make one minute invalid (stale) to exercise the reason path
    i = res.index[res["market_ts"] > pd.Timestamp("2026-10-02 08:00", tz="UTC")][0]
    res.loc[i, ["valid", "invalid_reason", "calc_value"]] = [False, "stale>300s:AAA,BBB", np.nan]
    metrics = compute_metrics(res, days[0])
    meta = {"status": "provisional", "calibration_date": days[0].isoformat(), "generated_at": datetime.now(timezone.utc),
            "data_source": "test", "limitations": [{"title": "t", "text": "x </script> y"}]}
    payload = export_payload(res, cons, state, metrics, meta)
    return {"res": res, "cons": cons, "state": state, "metrics": metrics, "payload": payload, "days": days}


def test_payload_is_json_and_out_of_sample_only(run):
    p = run["payload"]
    s = json.dumps(p, allow_nan=False)            # strict JSON (no NaN)
    assert len(s) > 1000
    n = len(p["series"]["t"])
    assert n > 0 and all(len(v) == n for v in p["series"].values())
    # calibration day dropped, only later days animated
    assert {d["date"] for d in p["days"]} == {"2026-10-02", "2026-10-05"}
    assert sum(d["end"] - d["start"] for d in p["days"]) == n
    # Istanbul ISO timestamps
    assert all(t.endswith("+03:00") for t in p["series"]["t"])
    assert p["series"]["t"][0].startswith("2026-10-02T10:")


def test_metrics_from_data(run):
    m = run["metrics"]
    res = run["res"]
    lt = pd.to_datetime(res["market_ts"], utc=True).dt.tz_convert("Europe/Istanbul")
    oos = res[(lt.dt.date > run["days"][0]).to_numpy()]
    ok = oos["valid"].astype(bool) & np.isfinite(oos["diff_pct"].astype(float))
    assert m["n_compared"] == int(ok.sum())
    assert m["bias"]["mean_pct"] == pytest.approx(oos.loc[ok, "diff_pct"].mean())
    assert m["summary"]["mae_pts"] == pytest.approx(oos.loc[ok, "diff_pts"].abs().mean())
    assert m["bias"]["mean_pct"] == pytest.approx(0.03, abs=0.005)   # calc above official by ~0.03%
    assert m["invalid"]["by_reason"] == {"stale": 1}
    assert {r["symbol"] for r in m["invalid"]["by_symbol"]} == {"AAA", "BBB"}
    assert m["verdict"]["answer"] and m["verdict"]["headline"]
    assert 0 <= m["within_warn_pct"] <= 100


def test_constituent_weights_sum_to_100(run):
    rows = run["payload"]["constituents"]
    assert len(rows) == 3
    assert sum(r["weight_pct"] for r in rows) == pytest.approx(100.0)
    assert run["payload"]["divisor"]["value"] == pytest.approx(run["state"].divisor)


def test_build_html_roundtrip(run, tmp_path):
    out = build_html(run["payload"], tmp_path / "r.html")
    txt = out.read_text(encoding="utf-8")
    assert txt.startswith("<!doctype html>")
    assert "/*__CSS__*/" not in txt and "/*__JS__*/" not in txt and "__PAYLOAD__" not in txt
    # offline: no external scripts / stylesheets / fonts
    assert not re.search(r'<script[^>]+src=|<link[^>]+href=|@import|url\(\s*["\']?https?:', txt)
    # payload survives embedding, including a "</script>" inside the data
    back = extract_payload(txt)
    assert back == json.loads(json.dumps(run["payload"]))
    assert txt.count("</script>") == 2
    assert 'lang="tr"' in txt and "prefers-color-scheme: dark" in txt


def test_reason_key():
    assert reason_key("stale>300s:AKBNK") == "stale"
    assert reason_key("price_jump>10%:X") == "price_jump"
    assert reason_key("outside_price_limit:ASTOR") == "outside_price_limit"


# ------------------------------------------------------------------ citations
def _pages() -> dict[int, str]:
    txt = RULESET.read_text(encoding="utf-8")
    parts = re.split(r"===== PAGE (\d+) =====", txt)
    norm = lambda s: re.sub(r"\s+", " ", s)  # noqa: E731
    return {int(parts[i]): norm(parts[i + 1]) for i in range(1, len(parts), 2)}


@pytest.mark.skipif(not RULESET.exists(), reason="docs/kural_seti.txt missing")
def test_rule_quotes_on_stated_pdf_page():
    pages = _pages()
    norm = lambda s: re.sub(r"\s+", " ", s)  # noqa: E731
    n = 0
    for step in RULE_STEPS:
        for q in step["quotes"]:
            page = pages[q["pdf_page"]]
            if q["verbatim"]:
                assert norm(q["text"]) in page, (q["ref"], q["pdf_page"], q["text"][:50])
            else:
                for tok in q["check_tokens"]:
                    assert norm(tok) in page, (q["ref"], tok)
            n += 1
    assert n >= 12


@pytest.mark.skipif(not RULESET.exists(), reason="docs/kural_seti.txt missing")
def test_section_headings_on_stated_pdf_page():
    pages = _pages()
    heads = {"2.2": "2.2. Pay Sayısı", "2.5": "2.5. Fiili Dolaşımdaki Pay Oranı", "2.6": "2.6. Ağırlık Katsayısı",
             "2.13": "2.13. Endekslerin Versiyonları", "2.16": "2.16. Değerleme Dönemi",
             "2.17": "2.17. Endeks Dönemi", "2.18": "2.18. Hesaplanma ve Yayım Sıklığı",
             "2.19": "2.19. Veri Hassasiyeti", "2.21": "2.21. Sınırlama Parametreleri",
             "3.3": "3.3. BIST 30 Endeksi", "4": "4. HESAPLAMA FORMÜLÜ", "5.1": "5.1. Madde 5.2",
             "5.2": "5.2. Endekslerin bölen değerinde", "5.2.f": "f) Fiili Dolaşımdaki Pay Oranının"}
    for step in RULE_STEPS:
        for r in step["refs"]:
            first = int(str(r["pdf_page"]).split("–")[0])
            assert heads[r["section"]] in pages[first], (r["section"], first)
            assert r["printed_page"] == "–".join(str(int(p) - 1) for p in str(r["pdf_page"]).split("–"))


def test_result_columns_contract(run):
    assert set(INDEX_RESULT_COLUMNS) <= set(run["res"].columns)
