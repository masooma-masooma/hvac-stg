"""
Telemetry staleness detection and Safe Fallback Mode.

The controller only optimises on fresh data. If the newest telemetry row is
older than STALE_AFTER_SEC (or none can be read), it stops running the MPC and
commands a conservative fixed action instead.
"""

from datetime import datetime, timezone
from typing import Optional

STALE_AFTER_SEC = 30.0
SAFE_SETPOINT_C = 21.0
SAFE_DAMPER = 1


def parse_timestamp(ts: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def telemetry_age_seconds(latest_ts: Optional[str], now: Optional[datetime] = None) -> float:
    """Age of the newest telemetry row in seconds (infinity when unknown)."""
    if not latest_ts:
        return float("inf")
    dt = parse_timestamp(latest_ts)
    if dt is None:
        return float("inf")
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds())


def is_stale(age_seconds: float, threshold: float = STALE_AFTER_SEC) -> bool:
    return age_seconds > threshold


def safe_fallback_command(age_seconds: float) -> dict:
    age = "unknown" if age_seconds == float("inf") else f"{age_seconds:.0f}s"
    return {
        "setpoint": SAFE_SETPOINT_C,
        "damper": SAFE_DAMPER,
        "reason": f"SAFE FALLBACK: telemetry stale (age {age} > {STALE_AFTER_SEC:.0f}s). "
                  f"Holding {SAFE_SETPOINT_C}C / damper {SAFE_DAMPER} until fresh data returns.",
    }
