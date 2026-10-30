"""Tests for bist30.metadata (constituents + corporate actions)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from bist30.metadata import (
    DEFAULT_CONSTITUENTS_PATH,
    MetadataError,
    constituents_version,
    load_constituents,
    load_constituents_frame,
    load_corporate_actions,
)
from bist30.metadata.build import read_index_list, round_fdpo
from bist30.models import CONSTITUENT_COLUMNS, CORPORATE_ACTION_COLUMNS, Constituent

AS_OF = date(2026, 10, 30)
RAW_INDEX_LIST = DEFAULT_CONSTITUENTS_PATH.parent / "raw" / "hisse_endeks_ds_20261030.csv"


# ---------------------------------------------------------------- real files
def test_constituents_file_has_exact_columns():
    df = pd.read_csv(DEFAULT_CONSTITUENTS_PATH, dtype=str, keep_default_na=False)
    assert list(df.columns) == CONSTITUENT_COLUMNS


def test_load_constituents_30_valid():
    cons = load_constituents(as_of=AS_OF)
    assert len(cons) == 30
    assert all(isinstance(c, Constituent) for c in cons)
    syms = [c.symbol for c in cons]
    assert len(set(syms)) == 30
    for c in cons:
        assert c.shares_outstanding > 0
        assert 0 < c.free_float_ratio <= 1
        assert c.weight_factor == 1.0          # plain XU030 is uncapped
        assert c.yahoo_ticker == f"{c.symbol}.IS"
        assert c.effective_from <= AS_OF


def test_constituents_match_official_borsa_list():
    official = set(read_index_list(RAW_INDEX_LIST).symbol)
    assert len(official) == 30
    assert {c.symbol for c in load_constituents(as_of=AS_OF)} == official


def test_period_change_trmet_in_dstkf_out():
    syms = {c.symbol for c in load_constituents(as_of=date(2026, 10, 1))}
    assert "TRMET" in syms and "DSTKF" not in syms


def test_every_row_has_sources():
    df = load_constituents_frame(as_of=AS_OF)
    assert (df.shares_source.str.len() > 10).all()
    assert (df.ff_source.str.len() > 10).all()


def test_ff_precision_rule_2_19():
    df = load_constituents_frame(as_of=AS_OF)
    pct = df.free_float_ratio * 100
    big = pct[pct >= 1]
    assert ((big - big.round()).abs() < 1e-9).all()


def test_petkm_ff_change_effective_2026_10_28():
    before = {c.symbol: c for c in load_constituents(as_of=date(2026, 10, 27))}
    after = {c.symbol: c for c in load_constituents(as_of=date(2026, 10, 28))}
    assert after["PETKM"].free_float_ratio == pytest.approx(0.37)
    assert before["PETKM"].free_float_ratio > after["PETKM"].free_float_ratio


def test_outside_period_raises():
    with pytest.raises(MetadataError):
        load_constituents(as_of=date(2027, 1, 4))


def test_constituents_version_is_stable():
    v1, v2 = constituents_version(), constituents_version()
    assert v1 == v2 and v1.startswith("sha256:") and len(v1) == len("sha256:") + 16


def test_corporate_actions_contract():
    ca = load_corporate_actions()
    assert list(ca.columns) == CORPORATE_ACTION_COLUMNS
    assert ca.affects_price_index.dtype == bool
    # cash dividends never adjust the price-index divisor (rule 5.2.a)
    assert not ca.loc[ca.action_type == "cash_dividend", "affects_price_index"].any()
    inc = ca[(ca.action_type == "inclusion") & (ca.symbol == "TRMET")]
    assert len(inc) == 1 and inc.iloc[0].effective_date == date(2026, 10, 1)
    pk = ca[(ca.action_type == "ff_change") & (ca.symbol == "PETKM")]
    assert pk.iloc[-1].new_free_float_ratio == pytest.approx(0.37)


# ------------------------------------------------------------ validation
def _write(tmp_path: Path, df: pd.DataFrame) -> Path:
    p = tmp_path / "c.csv"
    df.to_csv(p, index=False)
    return p


@pytest.fixture
def base_df() -> pd.DataFrame:
    return pd.read_csv(DEFAULT_CONSTITUENTS_PATH, dtype=str, keep_default_na=False)


def test_validation_wrong_count(tmp_path, base_df):
    active = load_constituents_frame(as_of=AS_OF).symbol.tolist()
    df = base_df[base_df.symbol != active[0]]
    with pytest.raises(MetadataError, match="exactly 30"):
        load_constituents(as_of=AS_OF, path=_write(tmp_path, df))


def test_validation_duplicate(tmp_path, base_df):
    df = base_df.copy()
    extra = df[df.symbol == "AKBNK"].copy()
    extra["effective_from"] = "2026-10-15"
    df = pd.concat([df, extra])
    with pytest.raises(MetadataError):
        load_constituents(as_of=AS_OF, path=_write(tmp_path, df))


@pytest.mark.parametrize("col,val", [("free_float_ratio", "1.2"), ("free_float_ratio", "0"),
                                     ("shares_outstanding", "0"), ("shares_outstanding", "-5")])
def test_validation_bad_numbers(tmp_path, base_df, col, val):
    df = base_df.copy()
    df.loc[df.symbol == "THYAO", col] = val
    with pytest.raises(MetadataError):
        load_constituents(as_of=AS_OF, path=_write(tmp_path, df))


def test_validation_missing_column(tmp_path, base_df):
    with pytest.raises(MetadataError, match="missing columns"):
        load_constituents(as_of=AS_OF, path=_write(tmp_path, base_df.drop(columns=["notes"])))


def test_round_fdpo():
    assert round_fdpo(37.32) == pytest.approx(0.37)
    assert round_fdpo(48.5) == pytest.approx(0.49)      # half-up, not banker's rounding
    assert round_fdpo(5.661) == pytest.approx(0.06)
    assert round_fdpo(0.456) == pytest.approx(0.0046)   # <1%: two decimals


def test_ff_estimates_respect_rule_5_2_f_threshold():
    """No-notice FDPOs must stay inside the 5/10-pt band vs every later weekly MKK file,
    otherwise Borsa would have changed them (and a notice would exist)."""
    from bist30.metadata.build import INDEX_FDPO_NOTICES, RAW_DIR, read_mkk, threshold_pts

    df = load_constituents_frame(as_of=AS_OF).set_index("symbol")
    files = sorted((RAW_DIR / "mkk_fdpo").glob("Fiili_Dolasim_Raporu_MKK-*.xlsx"))
    later = [f for f in files if pd.to_datetime(f.stem[-10:], dayfirst=True) >= pd.Timestamp("2026-09-11")]
    assert later
    for f in later:
        mkk = read_mkk(f)
        for sym, r in df.iterrows():
            if sym in INDEX_FDPO_NOTICES:
                continue
            h = r.free_float_ratio * 100
            assert abs(h - float(mkk.loc[sym, "ff_pct"])) < threshold_pts(h), (sym, f.name)
