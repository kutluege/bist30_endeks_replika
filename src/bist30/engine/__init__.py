"""Index engine: as-of synchronised XU030 calculation, divisor management, diagnostics.

Public API (see docs/engine.md, docs/INTERFACES.md):
    compute_index_series, IndexEngine, attach_official
    calibrate_divisor, adjust_divisor, apply_index_events,
    load_divisor_state, save_divisor_state
    lag_diagnostics, replication_error_summary
    session_phase, SessionConfig
"""

from .core import (EXTRA_RESULT_COLUMNS, RESULT_COLUMNS, IndexEngine, LatencyMixError,
                   attach_official, compute_index_series, normalize_daily_closes)
from .diagnostics import lag_diagnostics, replication_error_summary
from .divisor import (CalibrationError, DivisorAdjustmentError, active_constituents,
                      adjust_divisor, apply_index_events, asof_prices, calibrate_divisor,
                      constituents_fingerprint, load_divisor_state, raw_mcap,
                      save_divisor_state)
from .session import SessionConfig, session_phase, trading_date

__all__ = [
    "compute_index_series", "IndexEngine", "attach_official", "normalize_daily_closes", "LatencyMixError",
    "RESULT_COLUMNS", "EXTRA_RESULT_COLUMNS",
    "calibrate_divisor", "adjust_divisor", "apply_index_events", "load_divisor_state",
    "save_divisor_state", "CalibrationError", "DivisorAdjustmentError", "asof_prices",
    "raw_mcap", "active_constituents", "constituents_fingerprint",
    "lag_diagnostics", "replication_error_summary",
    "SessionConfig", "session_phase", "trading_date",
]
