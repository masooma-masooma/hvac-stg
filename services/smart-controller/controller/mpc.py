"""
Model Predictive Control (MPC) Optimization Engine
Receding-horizon optimisation over N = 6 steps of 5 minutes (30 minutes), balancing
energy, thermal comfort, indoor air quality (CO2) and actuator wear.

- Model 01 (Random Forest occupancy forecaster) supplies the occupancy expected in
  each horizon step.
- Model 02 (gradient-boosted thermal/IAQ predictor) rolls every candidate action
  sequence forward and supplies the predicted temperature, CO2 and power.

Action sequences use move blocking: the (setpoint, damper) pair may change at the
start of three blocks, by default steps 1, 2 and 4 (blocks of 1, 2 and 3 steps).
When Model 01 forecasts the room becoming occupied or empty at a later step, the
third block starts exactly at that step so that empty and occupied periods get
their own action. This gives 20^3 = 8000 candidate sequences that are evaluated
in vectorised batches. Only the first action is applied; the problem is re-solved
every control cycle.

Stage cost for step k (dt_h = step length in hours):
  occupied:   w_energy*P_k[kW]*dt_h + w_temp*(T_k - 21.5)^2*dt_h + w_band*viol(T_k)^2*dt_h
              + w_co2*(max(0, CO2_k - 800)/100)^2*dt_h + w_co2_hard*(max(0, CO2_k - 1000)/100)^2*dt_h
  unoccupied: w_energy*P_k[kW]*dt_h + w_band*max(0, 16 - T_k)^2*dt_h
plus a switching cost w_delta*(dSetpoint)^2 + w_damper*|dDamper| at each block change.

A dwell-time filter (2 control cycles by default) holds the previous action when
a new one is requested too soon, unless the room is in an emergency state.
"""

import time
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any, Optional

import numpy as np

from models.occupancy_forecaster import OccupancyForecaster
from models.thermal_model import ThermalPredictor
from models.dynamics import BuildingDynamics, COMFORT_MIN, COMFORT_MAX

OCCUPIED_TARGET_C = 21.5
FROST_LIMIT_C = 16.0
CO2_SOFT_PPM = 800.0
CO2_HARD_PPM = 1000.0


class MPCOptimizer:
    def __init__(self,
                 dynamics=None,
                 occupancy_forecaster=None,
                 thermal_predictor=None,
                 horizon_steps: int = 6,
                 step_dt_seconds: float = 300.0,
                 dwell_time_seconds: float = 20.0,
                 move_blocks: Tuple[int, ...] = (1, 2, 3)):
        self.dynamics = dynamics or BuildingDynamics()
        self.occupancy_forecaster = occupancy_forecaster or OccupancyForecaster()
        self.thermal_predictor = thermal_predictor or ThermalPredictor()
        self.horizon = horizon_steps
        self.step_dt = step_dt_seconds
        self.dwell_time = dwell_time_seconds
        if sum(move_blocks) != horizon_steps:
            move_blocks = (1, horizon_steps - 1) if horizon_steps > 1 else (1,)
        self.move_blocks = tuple(move_blocks)

        self.candidate_setpoints = [18.0, 19.5, 20.5, 21.5, 22.5]
        self.candidate_dampers = [0, 1, 2, 3]
        self.actions = [(sp, d) for sp in self.candidate_setpoints for d in self.candidate_dampers]

        self.w_energy = 1.0       # per kWh
        self.w_temp = 0.5         # per degC^2 h away from the occupied target
        self.w_band = 20.0        # per degC^2 h outside the 20-24 degC band (or below frost limit)
        self.w_co2 = 0.5          # per (100 ppm)^2 h above 800 ppm
        self.w_co2_hard = 10.0    # per (100 ppm)^2 h above 1000 ppm
        self.w_delta = 0.002      # per degC^2 of setpoint change
        self.w_damper = 0.005     # per damper level change

        self.last_action_ts = 0.0
        self.last_setpoint = 21.0
        self.last_damper = 1
        self.last_forecast: List[int] = []
        self.last_blocks: Tuple[int, ...] = self.move_blocks

    def _stage_cost(self, temp, co2, power, occupied: bool) -> np.ndarray:
        dt_h = self.step_dt / 3600.0
        cost = self.w_energy * (power / 1000.0) * dt_h
        if occupied:
            viol = np.maximum(0.0, COMFORT_MIN - temp) + np.maximum(0.0, temp - COMFORT_MAX)
            cost = cost + self.w_temp * (temp - OCCUPIED_TARGET_C) ** 2 * dt_h
            cost = cost + self.w_band * viol ** 2 * dt_h
            cost = cost + self.w_co2 * (np.maximum(0.0, co2 - CO2_SOFT_PPM) / 100.0) ** 2 * dt_h
            cost = cost + self.w_co2_hard * (np.maximum(0.0, co2 - CO2_HARD_PPM) / 100.0) ** 2 * dt_h
        else:
            cost = cost + self.w_band * np.maximum(0.0, FROST_LIMIT_C - temp) ** 2 * dt_h
        return cost

    def _switch_cost(self, sp_prev, d_prev, sp_new, d_new) -> np.ndarray:
        return self.w_delta * (sp_new - sp_prev) ** 2 + self.w_damper * np.abs(d_new - d_prev)

    def blocks_for(self, schedule: List[int]) -> Tuple[int, ...]:
        """Align the last block boundary with the first forecast occupied/empty transition."""
        occupied = [o > 0 for o in schedule[:self.horizon]]
        change = next((k for k in range(2, self.horizon) if occupied[k] != occupied[k - 1]), None)
        if change is None or len(self.move_blocks) != 3:
            return self.move_blocks
        return (1, change - 1, self.horizon - change)

    def solve(self, current_temp: float, current_co2: float, schedule: List[int]) -> Dict[str, Any]:
        """Evaluate every move-blocked action sequence and return the cheapest plan."""
        act_sp = np.array([a[0] for a in self.actions])
        act_d = np.array([a[1] for a in self.actions], dtype=float)
        n_act = len(self.actions)

        temp = np.array([current_temp], dtype=float)
        co2 = np.array([current_co2], dtype=float)
        cost = np.zeros(1)
        prev_sp = np.array([self.last_setpoint])
        prev_d = np.array([float(self.last_damper)])
        block_choice: List[np.ndarray] = []          # action index per block, per sequence
        steps_t, steps_c, steps_p = [], [], []        # per-step predictions, per sequence

        blocks = self.blocks_for(schedule)
        self.last_blocks = blocks
        k = 0
        for block_len in blocks:
            n_prev = len(temp)
            # Expand every existing prefix with every candidate action.
            temp = np.repeat(temp, n_act)
            co2 = np.repeat(co2, n_act)
            cost = np.repeat(cost, n_act)
            prev_sp = np.repeat(prev_sp, n_act)
            prev_d = np.repeat(prev_d, n_act)
            block_choice = [np.repeat(b, n_act) for b in block_choice]
            steps_t = [np.repeat(s, n_act) for s in steps_t]
            steps_c = [np.repeat(s, n_act) for s in steps_c]
            steps_p = [np.repeat(s, n_act) for s in steps_p]
            choice = np.tile(np.arange(n_act), n_prev)
            block_choice.append(choice)
            sp, d = act_sp[choice], act_d[choice]

            cost = cost + self._switch_cost(prev_sp, prev_d, sp, d)
            prev_sp, prev_d = sp, d

            for _ in range(block_len):
                occ = float(schedule[k])
                temp, co2, power = self.thermal_predictor.predict_batch(temp, co2, sp, d, occ, self.step_dt)
                cost = cost + self._stage_cost(temp, co2, power, occ > 0)
                steps_t.append(temp)
                steps_c.append(co2)
                steps_p.append(power)
                k += 1

        best = int(np.argmin(cost))
        plan_actions = [self.actions[int(b[best])] for b in block_choice]
        trajectory = []
        k = 0
        for block_len, (sp, d) in zip(blocks, plan_actions):
            for _ in range(block_len):
                trajectory.append({
                    "step": k + 1,
                    "minutes_ahead": int((k + 1) * self.step_dt / 60),
                    "setpoint": sp,
                    "damper": d,
                    "predicted_temp": round(float(steps_t[k][best]), 2),
                    "predicted_co2": round(float(steps_c[k][best]), 0),
                    "power_w": round(float(steps_p[k][best]), 1),
                    "occupancy": int(schedule[k]),
                })
                k += 1
        return {"setpoint": plan_actions[0][0], "damper": plan_actions[0][1],
                "cost": float(cost[best]), "trajectory": trajectory, "sequences": int(len(cost)),
                "blocks": list(blocks)}

    def optimize(self,
                 current_temp: float,
                 current_co2: float,
                 current_occupancy: int,
                 future_occupancy_schedule: Optional[List[int]] = None,
                 current_time: Optional[datetime] = None):
        now = time.time()
        now_dt = current_time or datetime.now(timezone.utc)

        if future_occupancy_schedule and len(future_occupancy_schedule) >= self.horizon:
            schedule = [int(o) for o in future_occupancy_schedule[:self.horizon]]
        else:
            schedule = self.occupancy_forecaster.predict_horizon(
                current_time=now_dt, current_occupancy=current_occupancy,
                horizon_steps=self.horizon, step_dt_sec=self.step_dt)
            # The first step starts now, so it can't be emptier than the room is.
            schedule[0] = max(schedule[0], int(current_occupancy))
        self.last_forecast = schedule

        plan = self.solve(current_temp, current_co2, schedule)
        opt_sp, opt_dmp, trajectory = plan["setpoint"], plan["damper"], plan["trajectory"]
        first = trajectory[0]
        step_min = int(self.step_dt / 60)

        if current_occupancy > 0:
            reason = (f"MPC: occupied ({current_occupancy}p) -> {opt_sp:.1f}C / damper {opt_dmp}. "
                      f"Model 02 predicts {first['predicted_temp']:.1f}C, {first['predicted_co2']:.0f} ppm in {step_min} min.")
        else:
            arrivals = [(i, o) for i, o in enumerate(schedule) if o > 0]
            if arrivals:
                i, o = arrivals[0]
                reason = (f"MPC: empty, Model 01 forecasts {o}p in {(i + 1) * step_min} min -> "
                          f"pre-conditioning plan, now {opt_sp:.1f}C / damper {opt_dmp}.")
            else:
                reason = (f"MPC: empty, Model 01 forecasts no arrivals in {self.horizon * step_min} min -> "
                          f"Eco setback {opt_sp:.1f}C / damper {opt_dmp}.")

        is_emergency = (current_temp < 18.0 and current_occupancy > 0) or (current_co2 > 1100)
        action_changed = (opt_sp != self.last_setpoint) or (opt_dmp != self.last_damper)
        time_since_last = now - self.last_action_ts

        if action_changed and time_since_last < self.dwell_time and not is_emergency:
            remaining = int(self.dwell_time - time_since_last) + 1
            return (self.last_setpoint, self.last_damper,
                    f"Dwell-time guardrail active ({remaining}s remaining). Suppressing chattering; "
                    f"MPC wanted {opt_sp:.1f}C / damper {opt_dmp}.", trajectory)

        if action_changed:
            self.last_action_ts = now
            self.last_setpoint = opt_sp
            self.last_damper = opt_dmp

        return opt_sp, opt_dmp, reason, trajectory

    def force_state(self, setpoint: float, damper: int):
        """Record an action applied outside the optimiser (e.g. safe fallback)."""
        if setpoint != self.last_setpoint or damper != self.last_damper:
            self.last_action_ts = time.time()
        self.last_setpoint = setpoint
        self.last_damper = damper
