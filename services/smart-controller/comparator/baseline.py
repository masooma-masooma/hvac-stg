"""
Fixed-Schedule Baseline Controller
Simulates a conventional commercial thermostat running a fixed schedule on a
virtual twin of Room A109 (same physics as the physical simulator), driven by the
same measured occupancy as the real room. Used as the live energy benchmark.
"""

import time
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from zoneinfo import ZoneInfo

from models.dynamics import BuildingDynamics

LOCAL_TZ = ZoneInfo("Europe/Stockholm")


def fixed_schedule_targets(dt: datetime) -> Tuple[float, int]:
    """
    Standard university building schedule (local time):
    06:00-22:00 -> 22.0 degC, damper 2; otherwise setback 16.0 degC, damper 0.
    """
    local = dt.astimezone(LOCAL_TZ)
    if 6 <= local.hour < 22:
        return 22.0, 2
    return 16.0, 0


class BaselineController:
    def __init__(self, dynamics: BuildingDynamics):
        self.dynamics = dynamics
        self.temp = 20.5
        self.co2 = 450.0
        self.cumulative_energy_kwh = 0.0
        self.comfort_violations_deg_sec = 0.0
        self.co2_violations_ppm_sec = 0.0
        self.last_update_ts = time.time()
        self.current_setpoint = 22.0
        self.current_damper = 2
        self.last_power_w = 0.0

    def reset(self, temp: Optional[float] = None, co2: Optional[float] = None):
        """Reset cumulative metrics; optionally re-synchronise the twin with the real room."""
        self.cumulative_energy_kwh = 0.0
        self.comfort_violations_deg_sec = 0.0
        self.co2_violations_ppm_sec = 0.0
        if temp is not None:
            self.temp = temp
        if co2 is not None:
            self.co2 = co2

    def get_scheduled_targets(self, dt: datetime) -> Tuple[float, int]:
        return fixed_schedule_targets(dt)

    def step(self, occupancy: int, dt_seconds: float = 10.0, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Advance the baseline twin by dt_seconds."""
        now = now or datetime.now(LOCAL_TZ)
        setpoint, damper = self.get_scheduled_targets(now)
        self.current_setpoint = setpoint
        self.current_damper = damper

        self.temp, self.co2, power_w = self.dynamics.step(
            temp=self.temp, co2=self.co2, setpoint=setpoint, damper=damper,
            occupancy=occupancy, dt_seconds=dt_seconds)
        self.last_power_w = power_w
        self.cumulative_energy_kwh += (power_w * dt_seconds) / 3600000.0

        # Comfort only matters while people are in the room.
        if occupancy > 0:
            temp_pen, co2_pen = self.dynamics.comfort_penalty(self.temp, self.co2)
            self.comfort_violations_deg_sec += (temp_pen ** 0.5) * dt_seconds
            self.co2_violations_ppm_sec += (co2_pen ** 0.5) * 100.0 * dt_seconds

        return {
            "temperature": round(self.temp, 2),
            "co2": round(self.co2, 0),
            "setpoint": setpoint,
            "damper": damper,
            "power_watts": round(power_w, 1),
            "cumulative_kwh": round(self.cumulative_energy_kwh, 5),
            "comfort_violation_degree_mins": round(self.comfort_violations_deg_sec / 60.0, 2),
            "co2_violation_ppm_mins": round(self.co2_violations_ppm_sec / 60.0, 2),
        }
