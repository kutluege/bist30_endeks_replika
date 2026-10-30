"""Rebuild data/metadata/constituents.csv from the raw official files.

Inputs (all free, official, saved under data/metadata/raw/):
  * hisse_endeks_ds_YYYYMMDD.csv  - Borsa Istanbul "Pay Endeks Raporu"
        https://www.borsaistanbul.com/datum/hisse_endeks_ds.csv  (index -> constituent list)
  * mkk_fdpo/Fiili_Dolasim_Raporu_MKK-DD-MM-YYYY.xlsx - MKK VAP "Tarih Bazinda Tum
        Sirketler Raporu" (https://www.vap.org.tr/fiili-dolasim-raporu): per ISIN total
        share count ("Toplam Pay Adedi") and free-float ratio ("Fiili Dolasim Orani").
  * INDEX_FDPO_NOTICES below - Borsa Istanbul KAP notices changing the FDPO used in
        index calculations (rule 5.2.f). The index FDPO is "sticky": it only follows
        the MKK weekly value when the move exceeds 5 pts (<=50%) / 10 pts (>50%), so
        a notice, when known, beats the latest MKK figure.

Usage:  python -m bist30.metadata.build [--mkk-date 23-10-2026] [--dry-run]
Requires openpyxl (for the MKK xlsx).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

from bist30.metadata.loader import DEFAULT_CONSTITUENTS_PATH, METADATA_DIR
from bist30.models import CONSTITUENT_COLUMNS

RAW_DIR = METADATA_DIR / "raw"
PERIOD_START = "2026-10-01"     # Oct-Dec 2026 index period (BIST duyuru 15598, 21.09.2026)
PERIOD_END = "2026-12-31"
INDEX_LIST_FILE = RAW_DIR / "hisse_endeks_ds_20261030.csv"
INDEX_LIST_URL = "https://www.borsaistanbul.com/datum/hisse_endeks_ds.csv"
PERIOD_ANNOUNCEMENT_URL = "https://borsaistanbul.com/duyuru/15598/bist-pay-endeksleri-donemsel-degisiklikleri"
MKK_URL = "https://www.vap.org.tr/fiili-dolasim-raporu (Tarih Bazinda Tum Sirketler Raporu)"
FF_BASE_DATE = "04-09-2026"   # last MKK week before the 11.09.2026 SPK method change


@dataclass(frozen=True)
class FdpoNotice:
    effective_from: str     # ISO date the index starts using the value
    pct: float              # FDPO in percent as announced by Borsa Istanbul
    source: str


# Borsa Istanbul index-FDPO notices relevant to the current constituents.
# Most recent notice per symbol wins from its effective date; earlier rows in the
# same list define the value used before it (inside the current period).
INDEX_FDPO_NOTICES: dict[str, list[FdpoNotice]] = {
    "TCELL": [FdpoNotice("2026-09-16", 39,
                         "Borsa Istanbul KAP FDPO notice 15.09.2026 (33 shares), eff. 16.09.2026; "
                         "reported https://www.borsamatik.com.tr/borsa-istanbulda-33-sirketin-fiili-dolasim-orani-degisiyor-haber-180784")],
    "PETKM": [FdpoNotice("2026-10-28", 37,
                         "Borsa Istanbul KAP FDPO notice 27.10.2026 (9 shares), eff. 28.10.2026; "
                         "reported https://www.yatirimx.com.tr/piyasa/bist-duyurdu-cwene-ve-petkm-dahil-9-hissenin-orani-degisiyor/27244")],
    "SASA": [FdpoNotice("2026-07-16", 44,
                        "Borsa Istanbul KAP FDPO notice 14.07.2026 (7 shares), eff. 16.07.2026; reported "
                        "https://www.hisse.net/haber/amp/sasa-nin-fiili-dolasim-orani-degisiyor-yeni-oran-belli-oldu-84775 "
                        "(MKK 23.10.2026 = 45.15%, below 5-pt threshold, so 44% assumed still in force)")],
}

# Value used before a notice that falls inside the period, when that earlier value
# is not itself documented by a notice: (symbol -> (pct, source/explanation)).
PRE_NOTICE_FDPO: dict[str, tuple[float, str]] = {
    "PETKM": (48, "ESTIMATE: no Borsa index-FDPO notice for PETKM found 2024-2026; MKK weekly FDPO was "
                  "48.03% (25.09), 47.46% (02.10), 47.33% (16.10.2026); 48% chosen (analyst report Apr-2026 "
                  "also shows FDO 48%). Uncertainty +/-1 pt (~0.6% index weight -> <2 bp level, absorbed by "
                  "divisor calibration)"),
}

NOTES: dict[str, str] = {
    "ISCTR": "N = C-group shares only (ISIN TRAISCTR91N2); A/B groups trade separately and are not in XU030.",
    "KRDMD": "N = D-group shares only (ISIN TRAKRDMR91G7); KRDMA/KRDMB trade separately.",
    "ASTOR": ("Last index-FDPO notice found: 37% eff. 01.10.2025 (rotaborsa.com/?p=363245), but MKK is 42.7% "
              "(>5 pts away, which per rule 5.2.f would have triggered a later change) and the implied-divisor "
              "test strongly rejects 37% (daily std 6.7 bp vs 3.8 bp; intraday 3.3 vs 2.1 bp). MKK 43% used; "
              "a later unfound notice is assumed."),
    "TCELL": "Index FDPO 39% (notice) vs MKK 37.65% on 23.10.2026.",
    "SASA": "Index FDPO 44% (notice 14.07.2026) vs MKK 45.15% on 23.10.2026.",
    "PETKM": "FDPO change 28.10.2026 (Borsa notice): 48% (estimate) -> 37%.",
    "TRMET": "Joined XU030 on 01.10.2026 (periodic review, replaced DSTKF).",
}


def round_fdpo(pct: float) -> float:
    """Rule 2.19: integer percent if FDPO >= 1%, else 2 decimals. Returns a fraction."""
    q = Decimal("1") if pct >= 1 else Decimal("0.01")
    return float(Decimal(str(pct)).quantize(q, rounding=ROUND_HALF_UP)) / 100.0


def read_index_list(path: Path = INDEX_LIST_FILE, index_code: str = "XU030") -> pd.DataFrame:
    df = pd.read_csv(path, sep=";", skiprows=[1], dtype=str, encoding="cp1254")
    df.columns = ["code", "name", "index_code", "index_name", "index_name_en", "date"]
    df = df[df.index_code == index_code].copy()
    df["symbol"] = df.code.str.replace(r"\.E$", "", regex=True)
    return df[["symbol", "name", "date"]].reset_index(drop=True)


def read_mkk(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, header=2, engine="openpyxl")
    df.columns = ["tarih", "isin", "isin_name", "symbol", "issuer", "total_shares", "non_traded",
                  "traded", "excl_c", "excl_de", "ff_shares", "ff_pct"]
    return df.dropna(subset=["symbol"]).set_index("symbol")


def threshold_pts(pct: float) -> float:
    """Rule 5.2.f.i: 5 pts if FDPO <= 50%, else 10 pts."""
    return 5.0 if pct <= 50 else 10.0


def build(mkk_date: str = "23-10-2026", ff_base_date: str = FF_BASE_DATE) -> pd.DataFrame:
    """N from MKK ``mkk_date``; H (absent a Borsa notice) from MKK ``ff_base_date``.

    Why two dates: SPK's 03.09.2026 decision (54/1626) changed MKK's FDPO method from
    11.09.2026. Rule 5.2.f only changes the index FDPO when the MKK figure moves by the
    threshold, and Borsa's weekly notices of 15.09-27.10.2026 list none of the shares
    below, so the index keeps its pre-11.09 FDPO. The last pre-change MKK week
    (04.09.2026) rounded per 2.19 is the best rule-based proxy for it. We assert that
    this value has stayed inside the threshold vs every later weekly MKK figure.
    """
    members = read_index_list()
    mkk_path = RAW_DIR / "mkk_fdpo" / f"Fiili_Dolasim_Raporu_MKK-{mkk_date}.xlsx"
    mkk = read_mkk(mkk_path)
    base_path = RAW_DIR / "mkk_fdpo" / f"Fiili_Dolasim_Raporu_MKK-{ff_base_date}.xlsx"
    mkk_base = read_mkk(base_path)
    iso = pd.to_datetime(mkk_date, dayfirst=True).date().isoformat()
    iso_b = pd.to_datetime(ff_base_date, dayfirst=True).date().isoformat()
    rows = []
    for m in members.itertuples(index=False):
        r = mkk.loc[m.symbol]
        n = float(r.total_shares)
        shares_src = (f"MKK VAP Fiili Dolasim Raporu {iso}, 'Toplam Pay Adedi' ISIN {r['isin']} "
                      f"({MKK_URL}); file raw/mkk_fdpo/{mkk_path.name}")
        b_pct = float(mkk_base.loc[m.symbol, "ff_pct"])
        mkk_h = round_fdpo(b_pct)
        gap = abs(mkk_h * 100 - float(r.ff_pct))
        if m.symbol not in INDEX_FDPO_NOTICES and gap >= threshold_pts(mkk_h * 100):
            raise ValueError(f"{m.symbol}: base FDPO {mkk_h:.2%} vs MKK {iso} {float(r.ff_pct):.2f}% "
                             f"crosses the rule 5.2.f threshold -> a Borsa notice must exist; check KAP")
        mkk_src = (f"ESTIMATE (rule 5.2.f stickiness): last pre-SPK-method-change MKK weekly FDPO "
                   f"{iso_b} = {b_pct:.3f}% rounded per 2.19 (file raw/mkk_fdpo/{base_path.name}); "
                   f"MKK {iso} = {float(r.ff_pct):.3f}% (gap {gap:.2f} pts < threshold); no Borsa "
                   f"index-FDPO notice found in weekly lists 02.09-27.10.2026")
        base = dict(symbol=m.symbol, name=m.name.strip(), yahoo_ticker=f"{m.symbol}.IS",
                    shares_outstanding=int(n), weight_factor=1.0,
                    shares_source=shares_src, notes=NOTES.get(m.symbol, ""))
        notices = sorted(INDEX_FDPO_NOTICES.get(m.symbol, []), key=lambda x: x.effective_from)
        in_period = [x for x in notices if x.effective_from > PERIOD_START]
        before = [x for x in notices if x.effective_from <= PERIOD_START]
        start_h, start_src = mkk_h, mkk_src
        if before:
            start_h = before[-1].pct / 100.0
            start_src = f"Borsa index FDPO {before[-1].pct:g}%: {before[-1].source}"
        if in_period and not before and m.symbol in PRE_NOTICE_FDPO:
            pct, src = PRE_NOTICE_FDPO[m.symbol]
            start_h, start_src = pct / 100.0, src
        segments = [(PERIOD_START, start_h, start_src)]
        for x in in_period:
            segments.append((x.effective_from, x.pct / 100.0, f"Borsa index FDPO {x.pct:g}%: {x.source}"))
        for i, (eff, h, src) in enumerate(segments):
            end = PERIOD_END
            if i + 1 < len(segments):
                end = (pd.Timestamp(segments[i + 1][0]) - pd.Timedelta(days=1)).date().isoformat()
            rows.append({**base, "free_float_ratio": round(h, 4), "effective_from": eff,
                         "effective_to": end, "ff_source": src})
    out = pd.DataFrame(rows)[CONSTITUENT_COLUMNS]
    return out.sort_values(["symbol", "effective_from"]).reset_index(drop=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mkk-date", default="23-10-2026")
    ap.add_argument("--ff-base-date", default=FF_BASE_DATE)
    ap.add_argument("--out", default=str(DEFAULT_CONSTITUENTS_PATH))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    df = build(a.mkk_date, a.ff_base_date)
    if a.dry_run:
        print(df.to_string())
    else:
        df.to_csv(a.out, index=False, encoding="utf-8", lineterminator="\n")
        print(f"wrote {len(df)} rows -> {a.out}")


if __name__ == "__main__":
    main()
