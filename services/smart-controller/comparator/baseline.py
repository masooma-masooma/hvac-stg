"""
Fixed-Schedule Baseline Controller
Simulates a conventional commercial thermostat running a fixed schedule.
Used as the benchmark against which the Smart MPC Optimizer is evaluated.
"""

import time
from datetime import datetime, timezone
from typing import Dict, Any
from models.dynamics import BuildingDynamics

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
        self.last_power_w = 115.0

    def reset(self):
        """Reset cumulative metrics for fresh benchmark testing"""
        self.cumulative_energy_kwh = 0.0
        self.comfort_violations_deg_sec = 0.0
        self.co2_violations_ppm_sec = 0.0

    def get_scheduled_targets(self, dt: datetime) -> tuple[float, int]:
        """
        Standard university building active schedule:
        Active Operating Hours (06:00 to 22:00): 22.0°C, Damper level 2
        Nights / Off-Hours: Setback to 16.0°C, Damper level 0
        """
        hour = dt.hour
        is_operating_hours = (6 <= hour < 22)
        if is_operating_hours:
            return 22.0, 2
        else:
            return 16.0, 0

    def step(self, occupancy: int, dt_seconds: float = 10.0) -> Dict[str, Any]:
        """
        Advance the baseline simulation by dt_seconds using current time.
        """
        now = datetime.now(timezone.utc)
        setpoint, damper = self.get_scheduled_targets(now)
        self.current_setpoint = setpoint
        self.current_damper = damper

        # Step dynamics
        new_temp, new_co2, power_w = self.dynamics.step(
            temp=self.temp,
            co2=self.co2,
            setpoint=setpoint,
            damper=damper,
            occupancy=occupancy,
            dt_seconds=dt_seconds
        )
        self.temp = new_temp
        self.co2 = new_co2
        self.last_power_w = power_w

        # Accumulate kWh
        energy_kwh = (power_w * dt_seconds) / 3600000.0
        self.cumulative_energy_kwh += energy_kwh

        # Accumulate penalties
        temp_pen, co2_pen = self.dynamics.comfort_penalty(self.temp, self.co2)
        if temp_pen > 0:
            self.comfort_violations_deg_sec += (temp_pen ** 0.5) * dt_seconds
        if co2_pen > 0:
            self.co2_violations_ppm_sec += (co2_pen ** 0.5) * dt_seconds

        return {
            "temperature": round(self.temp, 2),
            "co2": round(self.co2, 0),
            "setpoint": setpoint,
            "damper": damper,
            "power_watts": round(power_w, 1),
            "cumulative_kwh": round(self.cumulative_energy_kwh, 5),
            "comfort_violation_degree_mins": round(self.comfort_violations_deg_sec / 60.0, 2),
            "co2_violation_ppm_mins": round(self.co2_violations_ppm_sec / 60.0, 2)
        }

