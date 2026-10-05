"""
Model Predictive Control (MPC) Optimization Engine
Multi-objective optimization balancing energy minimization, thermal comfort,
indoor air quality (CO2), and actuator longevity (dwell-time anti-chattering).
Driven by:
- Model 01: Random Forest Occupancy Forecaster (Weeks 4-5)
- Model 02: Polynomial Ridge Thermal Response Predictor (Weeks 6-7)
"""

import time
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any, Optional

from models.occupancy_forecaster import OccupancyForecaster
from models.thermal_model import ThermalPredictor
from models.dynamics import BuildingDynamics

class MPCOptimizer:
    def __init__(self,
                 dynamics=None,
                 occupancy_forecaster=None,
                 thermal_predictor=None,
                 horizon_steps: int = 6,
                 step_dt_seconds: float = 300.0,
                 dwell_time_seconds: float = 60.0):
        self.dynamics = dynamics or BuildingDynamics()
        self.occupancy_forecaster = occupancy_forecaster or OccupancyForecaster()
        self.thermal_predictor = thermal_predictor or ThermalPredictor()
        self.horizon = horizon_steps
        self.step_dt = step_dt_seconds
        self.dwell_time = dwell_time_seconds
        self.candidate_setpoints = [18.0, 19.5, 20.5, 21.5, 22.5, 24.0]
        self.candidate_dampers = [0, 1, 2, 3]
        self.w_energy = 1.0
        self.w_comfort = 15.0
        self.w_air = 8.0
        self.w_switch = 1.5
        self.last_action_ts = 0.0
        self.last_setpoint = 21.0
        self.last_damper = 1

    def optimize(self,
                 current_temp: float,
                 current_co2: float,
                 current_occupancy: int,
                 future_occupancy_schedule=None,
                 current_time=None):
        now = time.time()
        now_dt = current_time or datetime.now(timezone.utc)
        time_since_last = now - self.last_action_ts

        if not future_occupancy_schedule or len(future_occupancy_schedule) < self.horizon:
            future_occupancy_schedule = self.occupancy_forecaster.predict_horizon(
                current_time=now_dt,
                current_occupancy=current_occupancy,
                horizon_steps=self.horizon,
                step_dt_sec=self.step_dt
            )

        best_cost = float('inf')
        best_action = (self.last_setpoint, self.last_damper)
        best_trajectory = []

        for sp in self.candidate_setpoints:
            for dmp in self.candidate_dampers:
                cost = 0.0
                sim_t = current_temp
                sim_c = current_co2
                trajectory = []
                switch_cost = self.w_switch * (abs(sp - self.last_setpoint) + abs(dmp - self.last_damper) * 0.5)
                cost += switch_cost
                for k in range(self.horizon):
                    occ_k = future_occupancy_schedule[k]
                    action_sp = sp if k == 0 else (21.5 if occ_k > 0 else 18.0)
                    action_dmp = dmp if k == 0 else (2 if occ_k > 0 else 0)
                    sim_t, sim_c, power_w = self.thermal_predictor.predict_step(
                        temp=sim_t, co2=sim_c, setpoint=action_sp,
                        damper=action_dmp, occupancy=occ_k, dt_seconds=self.step_dt)
                    t_pen, c_pen = self.thermal_predictor.comfort_penalty(sim_t, sim_c)
                    occ_factor = 1.0 if occ_k > 0 else 0.1
                    cost += (self.w_energy * (power_w / 1000.0)) + \
                            (self.w_comfort * t_pen * occ_factor) + \
                            (self.w_air * c_pen * occ_factor)
                    trajectory.append({
                        "step": k + 1, "predicted_temp": round(sim_t, 2),
                        "predicted_co2": round(sim_c, 0), "power_w": round(power_w, 1),
                        "occupancy": occ_k
                    })
                if cost < best_cost:
                    best_cost = cost
                    best_action = (sp, dmp)
                    best_trajectory = trajectory

        opt_sp, opt_dmp = best_action
        max_future_occ = max(future_occupancy_schedule) if future_occupancy_schedule else 0

        if current_occupancy > 0:
            if current_co2 > 900:
                reason = f"MPC [Model 02]: Air quality defense - {current_occupancy}p, CO2={current_co2:.0f}ppm. Damper set to {opt_dmp}."
            else:
                reason = f"MPC [Model 02]: Active occupancy ({current_occupancy}p) - Maintaining ASHRAE comfort at {opt_sp:.1f}C."
        else:
            if max_future_occ > 0:
                reason = f"MPC [Model 01 Forecaster]: Upcoming arrival of {max_future_occ}p - Pre-conditioning to {opt_sp:.1f}C."
            else:
                reason = f"MPC [Model 01 Forecaster]: Zero occupancy forecasted - Eco setback at {opt_sp:.1f}C."

        is_emergency = (current_temp < 18.0 and current_occupancy > 0) or (current_co2 > 1100)
        action_changed = (opt_sp != self.last_setpoint) or (opt_dmp != self.last_damper)

        if action_changed and (time_since_last < self.dwell_time) and not is_emergency:
            return self.last_setpoint, self.last_damper, f"Dwell-time guardrail active ({int(self.dwell_time - time_since_last)}s remaining). Suppressing chattering.", best_trajectory

        if action_changed:
            self.last_action_ts = now
            self.last_setpoint = opt_sp
            self.last_damper = opt_dmp

        return opt_sp, opt_dmp, reason, best_trajectory