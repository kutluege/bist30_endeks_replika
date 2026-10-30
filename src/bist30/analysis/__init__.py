"""VİOP BIST 30 futures analytics and XU030 replication validation.

Owner: VİOP & Validation Agent. See docs/analysis.md.
"""

from ._common import Feasibility, LatencyMismatchError
from .basis import BASIS_COLUMNS, basis_frame, basis_summary
from .contracts import (
    DEFAULT_CALENDAR,
    MarketCalendar,
    active_contracts,
    contract_code,
    contract_from_code,
    daily_settlement_price,
    expiry_timestamp,
    final_settlement_price,
    front_contract,
    last_trading_day,
    price_limits,
    round_to_tick,
)
from .fair_value import convert_rate, implied_rate, theoretical_futures, year_fraction
from .leadlag import cross_correlation, lead_lag_analysis, price_discovery_vecm
from .metrics import delay_stats, replication_metrics, tracking_error
from .orderbook import DEPTH_STATUS, imbalance_predictive_eval, orderbook_features
from .plots import plot_basis, plot_correlogram, plot_error_hist, plot_intraday
from .report import build_report, standard_feasibility

__all__ = [
    "Feasibility", "LatencyMismatchError", "BASIS_COLUMNS", "basis_frame", "basis_summary",
    "DEFAULT_CALENDAR", "MarketCalendar", "active_contracts", "contract_code", "contract_from_code",
    "daily_settlement_price", "expiry_timestamp", "final_settlement_price", "front_contract",
    "last_trading_day", "price_limits", "round_to_tick", "convert_rate", "implied_rate",
    "theoretical_futures", "year_fraction", "cross_correlation", "lead_lag_analysis",
    "price_discovery_vecm", "delay_stats", "replication_metrics", "tracking_error", "DEPTH_STATUS",
    "imbalance_predictive_eval", "orderbook_features", "plot_basis", "plot_correlogram",
    "plot_error_hist", "plot_intraday", "build_report", "standard_feasibility",
]
