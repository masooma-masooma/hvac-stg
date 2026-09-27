"""
Model Predictive Control (MPC) Optimization Engine
Multi-objective optimization balancing energy minimization, thermal comfort,
indoor air quality (CO2), and actuator longevity (dwell-time anti-chattering).
"""

import time
from typing import List, Tuple, Dict, Any
from models.dynamics import BuildingDynamics

class MPCOptimizer:
    def __init__(self, 
                 dynamics: BuildingDynamics,
                 horizon_steps: int = 6,
                 step_dt_seconds: float = 300.0, # 5-minute lookahead steps
                 dwell_time_seconds: float = 60.0): # Anti-chattering minimum interval
        self.dynamics = dynamics
        self.horizon = horizon_steps
        self.step_dt = step_dt_seconds
        self.dwell_time = dwell_time_seconds
        
        # Candidate discrete action space
        self.candidate_setpoints = [18.0, 19.5, 20.5, 21.5, 22.5, 24.0]
        self.candidate_dampers = [0, 1, 2, 3]

        # Weights
        self.w_energy = 1.0       # Weight on electrical power (kW)
        self.w_comfort = 15.0     # High priority on human thermal comfort when occupied
        self.w_air = 8.0          # Priority on CO2 < 1000 ppm
        self.w_switch = 1.5       # Penalty on changing actuator states (anti-chattering)

        # State tracking for dwell time
        self.last_action_ts = 0.0
        self.last_setpoint = 21.0
        self.last_damper = 1

    def optimize(self, 
                 current_temp: float, 
                 current_co2: float, 
                 current_occupancy: int,
                 future_occupancy_schedule: List[int] = None) -> Tuple[float, int, str, List[Dict[str, Any]]]:
        """
        Evaluate candidate action sequences over the prediction horizon.
        Returns: (best_setpoint, best_damper, explanation_reason, trajectory)
        """
        now = time.time()
        time_since_last_actuation = now - self.last_action_ts

        # Generate future occupancy forecast if not provided
        if not future_occupancy_schedule or len(future_occupancy_schedule) < self.horizon:
            # Persistence forecast: assume current occupancy persists across horizon
            future_occupancy_schedule = [current_occupancy] * self.horizon

        best_cost = float('inf')
        best_action = (self.last_setpoint, self.last_damper)
        best_trajectory = []
        best_reason = "MPC: Holding current steady state"

        # Search optimal policy: evaluate candidate actions for step 1
        for sp in self.candidate_setpoints:
            for dmp in self.candidate_dampers:
                cost = 0.0
                sim_t = current_temp
                sim_c = current_co2
                trajectory = []

                # Actuator switching penalty (suppresses unnecessary oscillation)
                delta_sp = abs(sp - self.last_setpoint)
                delta_dmp = abs(dmp - self.last_damper)
                switch_cost = self.w_switch * (delta_sp + delta_dmp * 0.5)
                cost += switch_cost

                # Simulate across prediction horizon H
                for k in range(self.horizon):
                    occ_k = future_occupancy_schedule[k]

                    # In receding horizon control, candidate action applies at k=0;
                    # subsequent steps relax to steady-state policy
                    action_sp = sp if k == 0 else (21.5 if occ_k > 0 else 18.0)
                    action_dmp = dmp if k == 0 else (2 if occ_k > 0 else 0)

                    sim_t, sim_c, power_w = self.dynamics.step(
                        temp=sim_t,
                        co2=sim_c,
                        setpoint=action_sp,
                        damper=action_dmp,
                        occupancy=occ_k,
                        dt_seconds=self.step_dt
                    )

                    t_pen, c_pen = self.dynamics.comfort_penalty(sim_t, sim_c)

                    # When room is occupied, comfort penalties are strictly enforced
                    occ_factor = 1.0 if occ_k > 0 else 0.1
                    cost += (self.w_energy * (power_w / 1000.0)) + \
                            (self.w_comfort * t_pen * occ_factor) + \
                            (self.w_air * c_pen * occ_factor)

                    trajectory.append({
                        "step": k + 1,
                        "predicted_temp": round(sim_t, 2),
                        "predicted_co2": round(sim_c, 0),
                        "power_w": round(power_w, 1),
                        "occupancy": occ_k
                    })

                if cost < best_cost:
                    best_cost = cost
                    best_action = (sp, dmp)
                    best_trajectory = trajectory

        opt_sp, opt_dmp = best_action

        # Generate explainable control rationale
        if current_occupancy > 0:
            if current_co2 > 900:
                best_reason = f"MPC: Air quality defense - High occupancy ({current_occupancy}p) and CO2 ({current_co2:.0f}ppm). Damper set to {opt_dmp}."
            else:
                best_reason = f"MPC: Active occupancy ({current_occupancy}p) - Maintaining optimal ASHRAE comfort band at {opt_sp:.1f}°C."
        else:
            if future_occupancy_schedule[1] > 0 or future_occupancy_schedule[2] > 0:
                best_reason = f"MPC: Predictive pre-conditioning - Upcoming occupancy detected, ramping to {opt_sp:.1f}°C."
            else:
                best_reason = f"MPC: Eco setback - Room unoccupied, conserving energy at {opt_sp:.1f}°C."

        # Anti-chattering & Dwell-Time Filter
        # Don't switch if dwell time hasn't passed UNLESS there is a severe comfort emergency
        is_emergency = (current_temp < 18.0 and current_occupancy > 0) or (current_co2 > 1100)
        action_changed = (opt_sp != self.last_setpoint) or (opt_dmp != self.last_damper)

        if action_changed and (time_since_last_actuation < self.dwell_time) and not is_emergency:
            # Suppress chattering: keep previous actuator state
            return self.last_setpoint, self.last_damper, f"Dwell-time guardrail active ({int(self.dwell_time - time_since_last_actuation)}s remaining). Suppressing chattering.", best_trajectory

        # Update last action state
        if action_changed:
            self.last_action_ts = now
            self.last_setpoint = opt_sp
            self.last_damper = opt_dmp

        return opt_sp, opt_dmp, best_reason, best_trajectory

