"""Self-contained Turkish HTML report: computed vs official XU030, minute by minute.

    from bist30.report_html import export_payload, build_html, compute_metrics
    payload = export_payload(results, constituents, divisor_state, metrics, meta)
    build_html(payload, "reports/xu030_replikasyon.html")

End-to-end: ``scripts/build_report_html.py``.
"""

from .metrics import DEFAULT_THRESHOLDS, compute_metrics, oos_rows
from .payload import export_payload
from .render import build_html, extract_payload

__all__ = ["export_payload", "build_html", "extract_payload", "compute_metrics", "oos_rows",
           "DEFAULT_THRESHOLDS"]
