"""
Offline Scenario Benchmark: simulated LTU work week in Room A109
Compares four controllers on identical physics (models/dynamics.py, the same
equations as the Go physical simulator) and identical occupancy:

  1. Fixed schedule   - 22 degC / damper 2 from 06:00-22:00, 16 degC / damper 0 otherwise
  2. Rule-based       - reactive thermostat: occupied now -> 21.5 degC / damper 2, else 18 degC / damper 0
  3. Rule-based + DCV - as 2, but the damper follows measured CO2 (demand-controlled ventilation):
                        damper 1 below 700 ppm, 2 below 900 ppm, otherwise 3
  4. MPC              - the production MPCOptimizer with Model 01 forecasts and Model 02 predictions

The occupancy ground truth is a timetable week drawn with a different random seed
than Model 01's training data, including cancelled lectures, so the forecaster is
evaluated on days it has not seen. Controllers act every 5 minutes (the MPC step
length; the live system re-plans every 10 s); physics is integrated at 1 s.

Run inside the smart-controller container:
    docker exec smart-controller python /scripts/benchmark_scenarios.py
Results: data/benchmarks/scenario_benchmark.json
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

for candidate in ("/app", os.path.join(os.path.dirname(__file__), "..", "services", "smart-controller")):
    if os.path.isdir(os.path.join(candidate, "controller")):
        sys.path.insert(0, os.path.abspath(candidate))
        break

from models.dynamics import BuildingDynamics, COMFORT_MIN, COMFORT_MAX, CO2_LIMIT
from models.occupancy_forecaster import OccupancyForecaster, simulate_day_occupancy, LOCAL_TZ, SLOT_MIN
from models.thermal_model import ThermalPredictor
from controller.mpc import MPCOptimizer
from comparator.baseline import fixed_schedule_targets

SCENARIO_SEED = 2026
DAYS = 5
WEEK_START = datetime(2026, 10, 5, tzinfo=LOCAL_TZ)   # Monday
CONTROL_SEC = SLOT_MIN * 60
OUT_PATH = os.getenv("BENCHMARK_OUT", "/benchmarks/scenario_benchmark.json")


def occupancy_week():
    rng = np.random.default_rng(SCENARIO_SEED)
    return np.concatenate([simulate_day_occupancy(WEEK_START + timedelta(days=d), rng) for d in range(DAYS)])


def fixed_policy(now, occ, temp, co2):
    return fixed_schedule_targets(now)


def rule_policy(now, occ, temp, co2):
    return (21.5, 2) if occ > 0 else (18.0, 0)


def dcv_policy(now, occ, temp, co2):
    if occ == 0:
        return 18.0, 0
    return 21.5, (1 if co2 < 700 else 2 if co2 < 900 else 3)


def run(policy, occupancy, dynamics):
    temp, co2 = 21.0, 450.0
    stats = {"energy_kwh": 0.0, "energy_occupied_kwh": 0.0, "energy_unoccupied_kwh": 0.0,
             "comfort_violation_degc_h": 0.0, "occupied_s": 0, "occupied_in_band_s": 0,
             "co2_over_1000_ppm_h": 0.0, "occupied_co2_over_1000_s": 0, "occupied_co2_over_800_s": 0,
             "setpoint_changes": 0}
    last_action = None
    for slot, occ in enumerate(occupancy):
        now = WEEK_START + timedelta(minutes=slot * SLOT_MIN)
        sp, dmp = policy(now, int(occ), temp, co2)
        if last_action is not None and (sp, dmp) != last_action:
            stats["setpoint_changes"] += 1
        last_action = (sp, dmp)
        for _ in range(CONTROL_SEC):
            temp, co2, p = dynamics.step(temp, co2, sp, dmp, int(occ), dt_seconds=1.0)
            kwh = p / 3600000.0
            stats["energy_kwh"] += kwh
            if occ > 0:
                stats["energy_occupied_kwh"] += kwh
                stats["occupied_s"] += 1
                viol = max(0.0, COMFORT_MIN - temp) + max(0.0, temp - COMFORT_MAX)
                stats["comfort_violation_degc_h"] += viol / 3600.0
                stats["occupied_in_band_s"] += int(viol == 0.0)
                stats["co2_over_1000_ppm_h"] += max(0.0, co2 - CO2_LIMIT) / 3600.0
                stats["occupied_co2_over_1000_s"] += int(co2 > CO2_LIMIT)
                stats["occupied_co2_over_800_s"] += int(co2 > 800.0)
            else:
                stats["energy_unoccupied_kwh"] += kwh
    occ_s = max(1, stats["occupied_s"])
    return {
        "energy_kwh": round(stats["energy_kwh"], 3),
        "energy_occupied_kwh": round(stats["energy_occupied_kwh"], 3),
        "energy_unoccupied_kwh": round(stats["energy_unoccupied_kwh"], 3),
        "occupied_hours": round(stats["occupied_s"] / 3600.0, 2),
        "occupied_time_in_comfort_band_pct": round(100.0 * stats["occupied_in_band_s"] / occ_s, 2),
        "comfort_violation_degc_h": round(stats["comfort_violation_degc_h"], 3),
        "occupied_time_co2_over_1000_pct": round(100.0 * stats["occupied_co2_over_1000_s"] / occ_s, 2),
        "occupied_time_co2_over_800_pct": round(100.0 * stats["occupied_co2_over_800_s"] / occ_s, 2),
        "co2_over_1000_ppm_h": round(stats["co2_over_1000_ppm_h"], 2),
        "action_changes": stats["setpoint_changes"],
    }


def main():
    dynamics = BuildingDynamics()
    forecaster = OccupancyForecaster()
    predictor = ThermalPredictor()
    mpc = MPCOptimizer(dynamics=dynamics, occupancy_forecaster=forecaster, thermal_predictor=predictor,
                       horizon_steps=6, step_dt_seconds=300.0, dwell_time_seconds=0.0)
    occupancy = occupancy_week()

    forecast_err = []
    solve_times = []

    def mpc_policy(now, occ, temp, co2):
        t0 = time.perf_counter()
        sp, dmp, _, _ = mpc.optimize(current_temp=temp, current_co2=co2, current_occupancy=occ, current_time=now)
        solve_times.append(time.perf_counter() - t0)
        slot = int((now - WEEK_START).total_seconds() // CONTROL_SEC)
        for k, pred in enumerate(mpc.last_forecast[1:], start=2):
            if slot + k - 1 < len(occupancy):
                forecast_err.append(pred - occupancy[slot + k - 1])
        return sp, dmp

    results = {}
    for name, policy in (("fixed_schedule", fixed_policy), ("rule_based", rule_policy),
                         ("rule_based_dcv", dcv_policy), ("mpc", mpc_policy)):
        t0 = time.time()
        results[name] = run(policy, occupancy, dynamics)
        print(f"{name:15s} {results[name]}  ({time.time() - t0:.0f}s)", flush=True)

    def saving(a, b, key="energy_kwh"):
        return round(100.0 * (results[a][key] - results[b][key]) / results[a][key], 1)

    summary = {
        "scenario": {
            "description": "Simulated LTU work week (Mon-Fri) in Room A109, timetable with random lecture cancellations",
            "week_start_local": WEEK_START.isoformat(),
            "days": DAYS,
            "occupancy_seed": SCENARIO_SEED,
            "occupied_slots_pct": round(100.0 * float(np.mean(occupancy > 0)), 1),
            "peak_occupancy": int(occupancy.max()),
            "controller_interval_s": CONTROL_SEC,
            "physics_tick_s": 1,
            "outdoor_temp_c": dynamics.ambient_temp,
        },
        "controllers": results,
        "savings_pct": {
            "mpc_vs_fixed_total": saving("fixed_schedule", "mpc"),
            "mpc_vs_fixed_unoccupied": saving("fixed_schedule", "mpc", "energy_unoccupied_kwh"),
            "mpc_vs_fixed_occupied": saving("fixed_schedule", "mpc", "energy_occupied_kwh"),
            "rule_based_vs_fixed_total": saving("fixed_schedule", "rule_based"),
            "mpc_vs_rule_based_total": saving("rule_based", "mpc"),
            "rule_based_dcv_vs_fixed_total": saving("fixed_schedule", "rule_based_dcv"),
            "mpc_vs_rule_based_dcv_total": saving("rule_based_dcv", "mpc"),
        },
        "model_01_forecast_on_benchmark_week": {
            "mae_persons_10_to_30_min": round(float(np.mean(np.abs(forecast_err))), 3),
            "bias_persons": round(float(np.mean(forecast_err)), 3),
        },
        "mpc_solve_time_ms": {
            "mean": round(1000 * float(np.mean(solve_times)), 1),
            "p95": round(1000 * float(np.percentile(solve_times, 95)), 1),
        },
        "models": {"model_01": forecaster.metrics, "model_02": predictor.metrics},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    print(json.dumps(summary["savings_pct"], indent=2))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved {OUT_PATH}")


if __name__ == "__main__":
    main()
