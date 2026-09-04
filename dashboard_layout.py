"""Entrada compatível do layout. Contrato e implementação em dashboard_analytics."""
from dashboard_analytics import VERSION as LAYOUT_VERSION, publish


def ensure_dashboard(sh, metrics, now_brt):
    return publish(sh, metrics, now_brt)
