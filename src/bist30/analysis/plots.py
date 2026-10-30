"""Matplotlib figures (PNG) for the validation report. Istanbul-time x-axes.

Styling follows the project's data-viz rules: one y-scale per axes (no dual
axes; different units go to separate subplots), 2px lines, hairline recessive
grid, fixed categorical order (slot 1 blue = computed / primary series,
slot 2 orange = official / secondary), legend whenever >= 2 series, text in ink
colours (never series colours).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from bist30.models import ISTANBUL_TZ  # noqa: E402

from ._common import to_utc  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
NEUTRAL = "#f0efec"
S1 = "#2a78d6"   # blue
S2 = "#eb6834"   # orange
S3 = "#1baf7a"   # aqua

RC = {
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
    "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.labelsize": 9, "axes.grid": True, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False,
    "grid.color": GRID, "grid.linewidth": 0.8, "grid.linestyle": "-",
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK_2, "ytick.labelcolor": INK_2,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "lines.linewidth": 2.0, "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round",
    "legend.frameon": False, "legend.fontsize": 8, "legend.labelcolor": INK_2,
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
}


def _ist(ts) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(to_utc(pd.Series(ts)).dt.tz_convert(ISTANBUL_TZ))


def _time_axis(ax, idx: pd.DatetimeIndex) -> None:
    multi_day = len(idx) and (idx.max() - idx.min()) > pd.Timedelta(hours=20)
    fmt = "%d.%m %H:%M" if multi_day else "%H:%M"
    loc = mdates.AutoDateLocator(tz=ISTANBUL_TZ, minticks=4, maxticks=9)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt, tz=ISTANBUL_TZ))


def _legend_top(ax, ncols: int = 3) -> None:
    """Legend in its own band between title and plot area (never over the data)."""
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncols=ncols, borderaxespad=0.2)


def _subtitle(fig, text: str) -> None:
    fig.text(0.01, 0.005, text, color=MUTED, fontsize=7, ha="left", va="bottom")


def _save(fig, out_path) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(p, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return p


def _plot_segments(ax, x: pd.DatetimeIndex, y: np.ndarray, **kw):
    """Break lines across session gaps (> 30 min) so overnight is not interpolated."""
    s = pd.Series(y, index=x)
    gaps = np.r_[False, np.asarray((x[1:] - x[:-1]).total_seconds()) > 30 * 60]
    seg = np.cumsum(gaps)
    first = True
    for _, g in s.groupby(seg):
        ax.plot(g.index, g.to_numpy(), label=kw.pop("label", None) if first else None, **kw)
        first = False


def plot_intraday(index_result: pd.DataFrame, out_path, title: Optional[str] = None, day=None) -> Path:
    """Computed vs official XU030 on the same axes + error subplots (points and %).

    ``day``: Istanbul date to plot; None -> the last day in the frame (an intraday
    chart); "all" -> every day (overnight gaps are left blank, not interpolated)."""
    df = index_result.copy()
    df["market_ts"] = to_utc(df["market_ts"])
    df = df.sort_values("market_ts")
    days = df["market_ts"].dt.tz_convert(ISTANBUL_TZ).dt.date
    if day != "all" and len(df):
        sel = days.iloc[-1] if day is None else day
        df = df[days == sel]
        title = title or f"BIST 30: hesaplanan vs resmi · {sel:%d.%m.%Y}"
    x = _ist(df["market_ts"])
    calc = df["calc_value"].astype(float).where(df["valid"].astype(bool) if "valid" in df else True)
    off = df["official_value"].astype(float)
    e = calc - off
    ep = e / off * 100
    lat = ";".join(sorted(df["latency_class"].dropna().astype(str).unique())) if "latency_class" in df else "?"
    with plt.rc_context(RC):
        fig, axes = plt.subplots(3, 1, figsize=(10, 7.2), sharex=True,
                                 gridspec_kw={"height_ratios": [3, 1.3, 1.3], "hspace": 0.18})
        ax = axes[0]
        _plot_segments(ax, x, off.to_numpy(), color=S2, label="Resmi XU030")
        _plot_segments(ax, x, calc.to_numpy(), color=S1, label="Hesaplanan XU030", linewidth=1.6)
        ax.set_ylabel("Endeks (puan)")
        ax.set_title(title or "BIST 30: hesaplanan vs resmi (aynı market_ts)", pad=24)
        _legend_top(ax, 2)
        ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
        for a, series, lab in ((axes[1], e, "Fark (puan)"), (axes[2], ep, "Fark (%)")):
            a.axhline(0, color=AXIS, linewidth=1)
            _plot_segments(a, x, series.to_numpy(), color=S1, linewidth=1.4)
            a.set_ylabel(lab)
        _time_axis(axes[2], x)
        axes[2].set_xlabel("İstanbul saati")
        n = int(e.notna().sum())
        _subtitle(fig, f"Gecikme sınıfı: {lat} · karşılaştırılan gözlem: {n} · fark = hesaplanan − resmi")
        return _save(fig, out_path)


def plot_error_hist(index_result: pd.DataFrame, out_path, unit: str = "pts", bins: int = 40) -> Path:
    df = index_result
    ok = df["valid"].astype(bool) & df["official_value"].notna() if "valid" in df else df["official_value"].notna()
    e = (df.loc[ok, "calc_value"].astype(float) - df.loc[ok, "official_value"].astype(float))
    if unit == "pct":
        e = e / df.loc[ok, "official_value"].astype(float) * 100
    with plt.rc_context(RC):
        fig, ax = plt.subplots(figsize=(7, 3.8))
        if len(e.dropna()):
            ax.hist(e.dropna(), bins=bins, color=S1, edgecolor=SURFACE, linewidth=2)
            ax.axvline(0, color=AXIS, linewidth=1)
            ax.axvline(float(e.mean()), color=INK_2, linewidth=1)
            ax.annotate(f"ortalama {e.mean():+.3f}", (float(e.mean()), ax.get_ylim()[1] * 0.95),
                        xytext=(4, 0), textcoords="offset points", color=INK_2, fontsize=8, va="top")
        else:
            ax.text(0.5, 0.5, "Geçerli gözlem yok", transform=ax.transAxes, ha="center", color=MUTED)
        ax.set_title("Replikasyon hatası dağılımı")
        ax.set_xlabel("hesaplanan − resmi (" + ("%" if unit == "pct" else "puan") + ")")
        ax.set_ylabel("gözlem sayısı")
        ax.grid(axis="x", visible=False)
        _subtitle(fig, f"n = {int(e.notna().sum())}")
        return _save(fig, out_path)


def plot_basis(basis: pd.DataFrame, out_path, title: Optional[str] = None) -> Path:
    """Raw basis and carry-adjusted residual (points) + implied repo (%), separate axes."""
    if not basis.attrs.get("comparison_valid", True):
        raise ValueError(f"basis frame is flagged invalid: {basis.attrs.get('reason')}")
    df = basis.copy()
    df["market_ts"] = to_utc(df["market_ts"])
    df = df.sort_values("market_ts")
    x = _ist(df["market_ts"])
    v = df["valid"].astype(bool)
    with plt.rc_context(RC):
        fig, axes = plt.subplots(2, 1, figsize=(10, 5.8), sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1.2], "hspace": 0.18})
        ax = axes[0]
        ax.axhline(0, color=AXIS, linewidth=1)
        _plot_segments(ax, x, df["raw_basis_official"].where(v).to_numpy(), color=S1, label="Ham baz F − S")
        _plot_segments(ax, x, df["residual_official"].where(v).to_numpy(), color=S2,
                       label="Taşıma düzeltilmiş kalıntı F − F*")
        if df["calc_valid"].any():
            _plot_segments(ax, x, df["residual_calc"].where(df["calc_valid"]).to_numpy(), color=S3,
                           linewidth=1.4, label="Kalıntı (hesaplanan spot)")
        ax.set_ylabel("puan")
        ax.set_title(title or f"Baz analizi · {basis.attrs.get('contract', '')}", pad=24)
        _legend_top(ax, 3)
        a = axes[1]
        _plot_segments(a, x, (df["implied_repo_official"].where(v) * 100).to_numpy(), color=S1, linewidth=1.4)
        if df["rate"].notna().any():
            a.plot(x, df["rate"] * 100, color=MUTED, linewidth=1)
            a.annotate("girdi faiz", (x[-1], float(df["rate"].iloc[-1] * 100)), xytext=(4, 0),
                       textcoords="offset points", color=INK_2, fontsize=8, va="center")
        a.set_ylabel("ima edilen repo (%)")
        _time_axis(a, x)
        a.set_xlabel("İstanbul saati")
        _subtitle(fig, f"Gecikme sınıfı: {basis.attrs.get('latency_class', '?')} · "
                       f"{basis.attrs.get('compounding', '')} {basis.attrs.get('day_count', '')}")
        return _save(fig, out_path)


def plot_correlogram(ccf: pd.DataFrame, out_path, title: Optional[str] = None) -> Path:
    """Cross-correlation of futures vs spot returns by lag with ~95 % band."""
    with plt.rc_context(RC):
        fig, ax = plt.subplots(figsize=(7.5, 3.8))
        lags = ccf["lag"].to_numpy()
        ci = ccf["ci95"].to_numpy()
        ax.fill_between(lags, -ci, ci, color=NEUTRAL, step="mid", linewidth=0)
        ax.axhline(0, color=AXIS, linewidth=1)
        ax.vlines(lags, 0, ccf["corr"], color=S1, linewidth=2)
        ax.scatter(lags, ccf["corr"], s=36, color=S1, edgecolor=SURFACE, linewidth=2, zorder=3)
        ax.set_xlabel("gecikme k (bar) · k > 0: vadeli önde")
        ax.set_ylabel("korelasyon")
        ax.set_title(title or "Vadeli–spot getiri çapraz korelasyonu")
        ax.grid(axis="x", visible=False)
        _subtitle(fig, "Gri bant: ±1.96/√n")
        return _save(fig, out_path)
