"""Turkish markdown report builder (reports/summary.md)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from bist30.models import ISTANBUL_TZ

from ._common import Feasibility
from .contracts import SPEC_SOURCES, XU030_MULTIPLIER, XU030_TICK
from .orderbook import DEPTH_STATUS

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "summary.md"


def _f(x, nd: int = 3) -> str:
    if x is None:
        return "–"
    try:
        if isinstance(x, (float, np.floating)) and not np.isfinite(x):
            return "–"
        if isinstance(x, (int, np.integer)):
            return f"{int(x):,}".replace(",", ".")
        return f"{float(x):,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return str(x)


def _table(headers: Sequence[str], rows: Iterable[Sequence]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |")
    return "\n".join(out)


def standard_feasibility(*, have_spot_intraday: bool, spot_latency: Optional[str],
                         have_futures: bool, futures_latency: Optional[str], have_rate: bool,
                         have_depth: bool = False, have_trade_tape: bool = False,
                         have_tick_index: bool = False, extra: Sequence[Feasibility] = ()) -> list[Feasibility]:
    """Feasibility matrix derived from data availability (no numbers inside)."""
    same = have_futures and have_spot_intraday and futures_latency == spot_latency \
        and futures_latency not in (None, "unknown")
    rows = [
        Feasibility("Replikasyon (hesaplanan vs resmi XU030)", have_spot_intraday,
                    "" if have_spot_intraday else "gün içi pay/endeks verisi yok"),
        Feasibility("Adil değer (taşıma maliyeti)", have_rate and have_spot_intraday,
                    "" if have_rate else "doğrulanmış ücretsiz TL kısa vadeli faiz verisi yok (tahmin edilmez)"),
        Feasibility("Baz / kalıntı / ima edilen repo", same and have_rate,
                    "ücretsiz ve izinli VİOP gün içi fiyat kaynağı yok" if not have_futures else
                    (f"gecikme sınıfları farklı (vadeli={futures_latency}, spot={spot_latency})" if not same else
                     ("faiz verisi yok" if not have_rate else ""))),
        Feasibility("Lead-lag (çapraz korelasyon)", same,
                    "ücretsiz ve izinli VİOP gün içi fiyat kaynağı yok" if not have_futures else
                    ("" if same else f"gecikme sınıfları farklı (vadeli={futures_latency}, spot={spot_latency})")),
        Feasibility("Fiyat keşfi (Hasbrouck/Gonzalo-Granger lite)", same,
                    "lead-lag ile aynı veri gereksinimi" if not same else ""),
        Feasibility("Emir defteri dengesizliği → sonraki orta fiyat", have_depth,
                    "" if have_depth else DEPTH_STATUS + "; yalnızca sentetik veride test edildi"),
        Feasibility("Vade sonu uzlaşma fiyatı replikasyonu", have_tick_index or have_spot_intraday,
                    "" if have_tick_index else ("yaklaşık: 1 dk bar ile TWAP (borsa saniye bazlı endeks değerlerini kullanır)"
                                                if have_spot_intraday else "son 30 dk endeks verisi yok")),
        Feasibility("Günlük uzlaşma fiyatı replikasyonu", have_trade_tape,
                    "" if have_trade_tape else "VİOP işlem bazlı (tick) veri yok; ücretsiz kaynak bulunamadı"),
    ]
    return rows + list(extra)


def build_report(metrics: Optional[dict] = None, *, basis_summary: Optional[dict] = None,
                 experiments: Sequence[Feasibility] = (), figures: Optional[dict] = None,
                 delay: Optional[pd.DataFrame] = None, contracts: Sequence = (),
                 notes: Sequence[str] = (), out_path=None, title: str = "BIST 30 Replikasyon ve VİOP Analizi — Özet") -> Path:
    """Write the Turkish summary report. Every section degrades to an explicit
    'veri yok / yapılamadı' statement - nothing is filled with placeholders."""
    out_path = Path(out_path) if out_path else DEFAULT_REPORT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz=ISTANBUL_TZ)
    L: list[str] = [f"# {title}", "", f"_Oluşturma: {now:%Y-%m-%d %H:%M} (İstanbul)_", ""]

    # 1. replication
    L += ["## 1. Replikasyon metrikleri (hesaplanan − resmi, aynı market_ts)", ""]
    if not metrics:
        L += ["Motor çıktısı verilmedi; metrik hesaplanmadı.", ""]
    elif not str(metrics.get("status", "")).startswith("ok"):
        L += [f"**Hesaplanamadı:** {metrics.get('status')}", ""]
    else:
        m = metrics
        L += [_table(["Alan", "Değer"], [
            ("Dönem (UTC)", f"{m.get('start')} → {m.get('end')}"),
            ("Gecikme sınıfı", m.get("latency_class")),
            ("Fiyat kaynağı / resmi kaynak", f"{m.get('price_source')} / {m.get('official_source')}"),
            ("Toplam / geçerli / karşılaştırılan", f"{m['n_total']} / {m['n_valid']} / {m['n_compared']}"),
            ("Geçerli gözlem oranı", f"%{_f(m['pct_valid'], 1)}"),
        ]), ""]
        L += [_table(["Metrik", "Puan", "%"], [
            ("MAE", _f(m["mae_pts"]), _f(m["mae_pct"], 4)),
            ("RMSE", _f(m["rmse_pts"]), _f(m["rmse_pct"], 4)),
            ("Medyan mutlak hata", _f(m["median_abs_pts"]), _f(m["median_abs_pct"], 4)),
            ("95. yüzdelik mutlak hata", _f(m["p95_abs_pts"]), _f(m["p95_abs_pct"], 4)),
            ("Maks. mutlak hata", _f(m["max_abs_pts"]), _f(m["max_abs_pct"], 4)),
            ("Sapma (bias, ortalama işaretli)", _f(m["bias_pts"]), _f(m["bias_pct"], 4)),
        ]), ""]
        te = m.get("tracking", {})
        if te:
            rows = [("Getiri sayısı", te.get("n_returns")),
                    ("Gözlem başına izleme hatası (bp)", _f(te.get("te_per_obs_bps"), 3)),
                    ("Getiri korelasyonu", _f(te.get("return_corr"), 5))]
            if "te_annualised_pct" in te:
                rows.append(("Yıllık izleme hatası (günlük kapanış, %)", _f(te["te_annualised_pct"], 3)))
            L += [_table(["İzleme hatası", "Değer"], rows), ""]
        if m.get("invalid_reasons"):
            L += ["**Geçersiz gözlem nedenleri**", "",
                  _table(["Neden", "Adet"], sorted(m["invalid_reasons"].items(), key=lambda kv: -kv[1])), ""]
        st = m.get("staleness")
        if st:
            L += [_table(["Bayatlık (staleness)", "Değer"], [
                ("Bayat bileşen içeren satır oranı", f"%{_f(st['pct_rows_with_stale'], 1)}"),
                ("Ortalama / maks. bayat bileşen", f"{_f(st['mean_n_stale'], 2)} / {_f(st['max_n_stale'], 0)}"),
                ("Maks. bayatlık medyan / p95 / maks (sn)",
                 f"{_f(st['max_staleness_median_s'], 0)} / {_f(st['max_staleness_p95_s'], 0)} / {_f(st['max_staleness_max_s'], 0)}"),
                ("En sık bayat hisseler", ", ".join(f"{k} ({v})" for k, v in st.get("most_stale_symbols", {}).items()) or "–"),
            ]), ""]
        pdays = m.get("per_day")
        if isinstance(pdays, pd.DataFrame) and len(pdays):
            cols = [c for c in ("date", "n_total", "n_compared", "pct_valid", "mae_pts", "rmse_pts",
                                "max_abs_pts", "bias_pts", "mae_pct", "te_per_obs_bps") if c in pdays]
            L += ["**Gün bazında**", "", _table(cols, ([_f(r[c], 3) if c not in ("date",) else r[c]
                                                         for c in cols] for _, r in pdays.iterrows())), ""]
        if delay is None:
            delay = m.get("delay")

    # 2. delay
    L += ["## 2. Veri gecikmesi (received_ts − market_ts)", ""]
    if isinstance(delay, pd.DataFrame) and len(delay):
        cols = [c for c in delay.columns]
        L += [_table(cols, ([_f(r[c], 1) if isinstance(r[c], (float, np.floating)) else r[c] for c in cols]
                            for _, r in delay.iterrows())), "",
              "_Not: 1 saatten uzun gecikmeler geçmişe dönük doldurma (backfill) sayılır ve canlı istatistiklere girmez._", ""]
    else:
        L += ["Gecikme ölçümü yok (received_ts verilmedi).", ""]

    # 3. basis
    L += ["## 3. Baz ve adil değer", ""]
    if not basis_summary:
        L += ["Baz analizi yapılmadı (aşağıdaki fizibilite tablosuna bakınız).", ""]
    elif basis_summary.get("status") != "ok":
        L += [f"**Yapılamadı:** {basis_summary.get('status')}", ""]
    else:
        rows = []
        for k, lab in (("raw_basis_official", "Ham baz F − S (puan)"),
                       ("residual_official", "Kalıntı F − F* (puan)"),
                       ("residual_official_bps", "Kalıntı (bp)"),
                       ("implied_repo_official", "İma edilen repo (yıllık)"),
                       ("residual_calc_minus_official", "Kalıntı farkı: hesaplanan − resmi spot")):
            s = basis_summary.get(k)
            if s:
                rows.append((lab, _f(s["mean"], 4), _f(s["std"], 4), _f(s["min"], 4), _f(s["max"], 4), s["n"]))
        L += [_table(["Seri", "Ortalama", "Std", "Min", "Maks", "n"], rows), ""]

    # 4. contracts
    if contracts:
        L += ["## 4. VİOP sözleşmeleri", "",
              _table(["Kod", "Son işlem günü", "Çarpan (TL/puan)", "Fiyat adımı"],
                     ((c.code, c.expiry.isoformat(), _f(c.multiplier, 0), _f(c.tick_size, 2)) for c in contracts)), "",
              f"Kaynak: [VİOP endeks vadeli sözleşme özellikleri]({SPEC_SOURCES['index_futures_page']}), "
              f"[VİOP Prosedürü]({SPEC_SOURCES['viop_procedure']}).", ""]

    # 5. feasibility
    L += ["## 5. Deneylerin fizibilitesi", ""]
    if experiments:
        L += [_table(["Deney", "Durum", "Gerekçe"],
                     ((e.name, "yapılabilir" if e.feasible else "**yapılamaz**", e.reason or "–") for e in experiments)), ""]
        for e in experiments:
            if e.feasible and e.name.lower().startswith("lead") and isinstance(e.result, dict):
                s = e.result.get("summary", {})
                L += [f"- Lead-lag: n={s.get('n_returns')}, en güçlü gecikme={s.get('best_lag')} "
                      f"(ρ={_f(s.get('best_corr'), 3)}), lead oranı={_f(s.get('lead_ratio_fut_over_spot'), 2)}"]
    else:
        L += ["Fizibilite listesi verilmedi.", ""]

    # 6. figures
    if figures:
        L += ["", "## 6. Grafikler", ""]
        for name, p in figures.items():
            rel = os.path.relpath(Path(p), out_path.parent).replace("\\", "/")
            L += [f"### {name}", "", f"![{name}]({rel})", ""]
    if notes:
        L += ["## Notlar", ""] + [f"- {n}" for n in notes] + [""]
    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return out_path
