"""Cross-check of constituent metadata: weights and implied divisor per day.

implied_divisor_t = sum_i(P_i,t * N_i * H_i,t * K_i) / XU030_close_t

P_i,t  = official closing price from the Borsa Istanbul daily equity bulletin
         (https://www.borsaistanbul.com/data/thb/YYYY/MM/thbYYYYMMDD1.zip, saved under
         data/metadata/raw/bist_bulletin_thb/).
XU030  = official close (Yahoo ``XU030.IS`` daily close; equals the Borsa Istanbul
         PayEndeksleri.zip close, checked for 2026-10-28: 13960.89).

If N and H are right and no divisor event happened, the implied divisor is flat.
Because a divisor adjustment exactly absorbs a level change in N or H, a wrong N/H
only shows up as a *drift* proportional to that stock's relative performance.

Usage:  python -m bist30.metadata.check [--out data/metadata/implied_divisor_check.csv]
"""

from __future__ import annotations

import argparse
import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from bist30.metadata.loader import METADATA_DIR, load_constituents

THB_DIR = METADATA_DIR / "raw" / "bist_bulletin_thb"


def read_bulletin_closes(thb_dir: Path = THB_DIR) -> pd.DataFrame:
    """Long frame: date, symbol, close, prev_close, corp_action_flag, xu030_flag."""
    frames = []
    for z in sorted(Path(thb_dir).glob("thb*.zip")):
        with zipfile.ZipFile(z) as zf:
            raw = zf.read(zf.namelist()[0]).decode("cp1254", errors="replace")
        df = pd.read_csv(io.StringIO(raw), sep=";", skiprows=[1], low_memory=False)
        df = df[df["ISLEM  KODU"].astype(str).str.endswith(".E")]
        frames.append(pd.DataFrame({
            "date": pd.to_datetime(df["TARIH"]).dt.date,
            "symbol": df["ISLEM  KODU"].str[:-2],
            "close": pd.to_numeric(df["KAPANIS FIYATI"], errors="coerce"),
            "prev_close": pd.to_numeric(df["ONCEKI KAPANIS FIYATI"], errors="coerce"),
            # 6 = cash dividend, 3 = bonus issue, 1 = rights issue (observed codes)
            "corp_action_flag": pd.to_numeric(df["OZSERMAYE HALI"], errors="coerce").fillna(0).astype(int),
            "xu030_flag": pd.to_numeric(df["BIST 30 ENDEKS"], errors="coerce").fillna(0).astype(int),
        }))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_xu030_closes(start: date, end: date) -> pd.Series:
    import yfinance as yf

    d = yf.download("XU030.IS", start=start.isoformat(),
                    end=(pd.Timestamp(end) + pd.Timedelta(days=1)).date().isoformat(),
                    interval="1d", progress=False, auto_adjust=False)
    s = d["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s.index = pd.to_datetime(s.index).date
    return s.rename("xu030_close")


def implied_divisors(closes: pd.DataFrame, xu030: pd.Series, constituents_path=None) -> pd.DataFrame:
    rows = []
    for d, g in closes.groupby("date"):
        if d not in xu030.index:
            continue
        try:
            cons = load_constituents(as_of=d, path=constituents_path)
        except Exception:
            continue  # date outside the file's validity
        px = g.set_index("symbol")["close"]
        mcap = 0.0
        missing = []
        for c in cons:
            p = px.get(c.symbol)
            if p is None or pd.isna(p) or p <= 0:
                missing.append(c.symbol)
                continue
            mcap += p * c.shares_outstanding * c.free_float_ratio * c.weight_factor
        flagged = set(g.loc[g.xu030_flag == 1, "symbol"])
        listed = {c.symbol for c in cons}
        rows.append({
            "date": d, "raw_mcap": mcap, "xu030_close": float(xu030[d]),
            "implied_divisor": mcap / float(xu030[d]),
            "missing": ";".join(missing),
            "bulletin_membership_match": flagged == listed,
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out["chg_bp"] = out["implied_divisor"].pct_change() * 1e4
    return out


def weights(closes: pd.DataFrame, on: date, constituents_path=None) -> pd.DataFrame:
    cons = load_constituents(as_of=on, path=constituents_path)
    px = closes[closes.date == on].set_index("symbol")["close"]
    df = pd.DataFrame([{"symbol": c.symbol, "close": px.get(c.symbol), "N": c.shares_outstanding,
                        "H": c.free_float_ratio} for c in cons])
    df["ff_mcap"] = df.close * df.N * df.H
    df["weight_pct"] = df.ff_mcap / df.ff_mcap.sum() * 100
    return df.sort_values("weight_pct", ascending=False).reset_index(drop=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(METADATA_DIR / "implied_divisor_check.csv"))
    a = ap.parse_args(argv)
    closes = read_bulletin_closes()
    xu = fetch_xu030_closes(min(closes.date), max(closes.date))
    res = implied_divisors(closes, xu)
    res.to_csv(a.out, index=False, lineterminator="\n")
    pd.set_option("display.width", 200)
    print(res.to_string(float_format=lambda v: f"{v:,.2f}"))
    if not res.empty:
        rel = res.implied_divisor.std() / res.implied_divisor.mean() * 1e4
        print(f"implied divisor: mean {res.implied_divisor.mean():,.0f}  std {rel:.2f} bp  "
              f"max-min {(res.implied_divisor.max() / res.implied_divisor.min() - 1) * 1e4:.2f} bp")
        last = res.date.max()
        print(weights(closes, last).to_string(float_format=lambda v: f"{v:,.3f}"))


if __name__ == "__main__":
    main()
