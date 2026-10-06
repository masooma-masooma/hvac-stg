"""
Unit Tests for the Smart HVAC Optimization Controller
Run inside the container (tests/ is mounted at /tests):
    docker exec smart-controller pytest /tests/test_smart_controller.py -v

Covers:
1. Room physics (models/dynamics.py): setpoint tracking, occupant heat, CO2, power, penalties
2. MPC (controller/mpc.py): eco setback, no damper lock-in, occupied comfort/IAQ,
   pre-conditioning, dwell-time filter and its emergency bypass
3. Model 01 occupancy forecaster and Model 02 thermal predictor
4. Telemetry staleness detection and Safe Fallback Mode
5. Fixed-schedule baseline (local Swedish time, comfort counted only when occupied)
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

for candidate in ("/app", os.path.join(os.path.dirname(__file__), "..", "services", "smart-controller")):
    if os.path.isdir(os.path.join(candidate, "controller")):
        sys.path.insert(0, os.path.abspath(candidate))
        break

from models import dynamics as phys
from models.dynamics import BuildingDynamics
from models.occupancy_forecaster import OccupancyForecaster
from models.thermal_model import ThermalPredictor
from controller.mpc import MPCOptimizer
from controller import safety
from comparator.baseline import BaselineController, fixed_schedule_targets

STOCKHOLM = ZoneInfo("Europe/Stockholm")


@pytest.fixture(scope="module")
def dynamics():
    return BuildingDynamics()


@pytest.fixture(scope="module")
def forecaster():
    return OccupancyForecaster()


@pytest.fixture(scope="module")
def predictor():
    return ThermalPredictor()


@pytest.fixture
def mpc(dynamics, forecaster, predictor):
    return MPCOptimizer(dynamics=dynamics, occupancy_forecaster=forecaster, thermal_predictor=predictor,
                        horizon_steps=6, step_dt_seconds=300.0, dwell_time_seconds=20.0)


# ---------------------------------------------------------------- physics

def test_room_reaches_setpoint_without_offset(dynamics):
    t, _, _ = dynamics.step(temp=18.0, co2=450.0, setpoint=21.0, damper=1, occupancy=0, dt_seconds=600)
    assert abs(t - 21.0) < 0.05, f"Radiator thermostat must settle at the setpoint; got {t:.2f}"


def test_occupant_heat_is_realistic(dynamics):
    """A full lecture (25 people) with maximum ventilation must stay inside the comfort band."""
    t, _, _ = dynamics.step(temp=21.0, co2=600.0, setpoint=21.0, damper=3, occupancy=25, dt_seconds=600)
    assert 20.0 <= t <= 24.0, f"25 occupants with damper 3 drove the room to {t:.1f}C"
    _, _, empty = dynamics.step(temp=21.0, co2=600.0, setpoint=21.0, damper=2, occupancy=0, dt_seconds=300)
    _, _, occupied = dynamics.step(temp=21.0, co2=600.0, setpoint=21.0, damper=2, occupancy=12, dt_seconds=300)
    assert occupied < empty, "Occupant heat must reduce the radiator load"


def test_radiator_switches_off_above_setpoint(dynamics):
    t, _, power = dynamics.step(temp=24.0, co2=420.0, setpoint=18.0, damper=3, occupancy=0, dt_seconds=5)
    assert power == pytest.approx(25 + 135, abs=0.5), "Radiator must be off while the room is above setpoint"
    assert t < 24.0


def test_co2_generation_and_ventilation(dynamics):
    _, co2_closed, _ = dynamics.step(temp=21.0, co2=500.0, setpoint=21.0, damper=0, occupancy=10, dt_seconds=60)
    _, co2_vented, _ = dynamics.step(temp=21.0, co2=500.0, setpoint=21.0, damper=3, occupancy=10, dt_seconds=60)
    assert co2_closed > 500.0
    assert co2_vented < co2_closed


def test_power_reflects_setpoint_and_damper(dynamics):
    _, _, eco = dynamics.step(temp=18.0, co2=420.0, setpoint=18.0, damper=0, occupancy=0, dt_seconds=60)
    _, _, comfort = dynamics.step(temp=22.0, co2=420.0, setpoint=22.0, damper=2, occupancy=0, dt_seconds=60)
    assert eco == pytest.approx(25 + 0.005 * 6 * 8750, rel=1e-3)
    assert comfort > 3 * eco, "Holding 22C with damper 2 must cost far more than an 18C setback"


def test_comfort_penalties(dynamics):
    assert dynamics.comfort_penalty(22.0, 600.0) == (0.0, 0.0)
    t_cold, c_high = dynamics.comfort_penalty(18.0, 1200.0)
    assert t_cold > 0 and c_high > 0


# ---------------------------------------------------------------- MPC

def test_mpc_empty_room_eco_setback(mpc):
    sp, dmp, reason, trajectory = mpc.optimize(current_temp=21.0, current_co2=450.0, current_occupancy=0,
                                              future_occupancy_schedule=[0] * 6)
    assert len(trajectory) == 6
    assert sp <= 19.5, f"Empty room should set back; got {sp}"
    assert dmp == 0, f"Empty room with fresh air should not ventilate; got damper {dmp}"
    assert "Eco setback" in reason


def test_mpc_no_damper_lock_in(mpc):
    """Regression: switching cost used to pin the damper at 3 in an empty room indefinitely."""
    mpc.last_setpoint, mpc.last_damper, mpc.last_action_ts = 18.0, 3, 0.0
    _, dmp, _, _ = mpc.optimize(current_temp=17.5, current_co2=420.0, current_occupancy=0,
                                future_occupancy_schedule=[0] * 6)
    assert dmp <= 1, f"MPC stayed locked at a high damper level ({dmp}) in an empty room"


def test_mpc_occupied_room_comfort_and_air_quality(mpc):
    sp, dmp, reason, trajectory = mpc.optimize(current_temp=19.5, current_co2=950.0, current_occupancy=12,
                                              future_occupancy_schedule=[12] * 6)
    assert sp >= 20.5, f"Occupied room at 19.5C must be heated into the comfort band; got setpoint {sp}"
    assert dmp >= 2, f"12 people at 950 ppm need ventilation; got damper {dmp}"
    for step in trajectory:
        assert 20.0 <= step["predicted_temp"] <= 24.0, f"Planned temperature leaves the comfort band: {step}"
        assert step["predicted_co2"] < 1000.0, f"Planned CO2 exceeds 1000 ppm: {step}"
    assert "Model 02 predicts" in reason


def test_mpc_preconditions_before_forecast_arrival(mpc):
    _, _, reason, trajectory = mpc.optimize(current_temp=18.0, current_co2=420.0, current_occupancy=0,
                                            future_occupancy_schedule=[0, 0, 15, 15, 15, 15])
    assert "pre-conditioning" in reason
    assert trajectory[0]["damper"] == 0 and trajectory[1]["damper"] == 0, "Do not ventilate before anyone arrives"
    assert trajectory[2]["predicted_temp"] >= 20.0, f"Room must be comfortable when people arrive: {trajectory[2]}"
    assert trajectory[2]["predicted_co2"] < 1000.0, f"Ventilation must start when people arrive: {trajectory[2]}"


def test_mpc_blocks_follow_forecast_transition(mpc):
    assert mpc.blocks_for([0, 0, 15, 15, 15, 15]) == (1, 1, 4)
    assert mpc.blocks_for([10, 10, 10, 10, 0, 0]) == (1, 3, 2)
    assert mpc.blocks_for([0] * 6) == (1, 2, 3)


def test_mpc_dwell_time_filter(mpc):
    mpc.last_action_ts = time.time() - 5.0
    mpc.last_setpoint, mpc.last_damper = 18.0, 0
    sp, dmp, reason, _ = mpc.optimize(current_temp=19.0, current_co2=600.0, current_occupancy=1,
                                      future_occupancy_schedule=[1] * 6)
    assert reason.startswith("Dwell-time guardrail active")
    assert (sp, dmp) == (18.0, 0)


def test_mpc_dwell_bypassed_in_emergency(mpc):
    mpc.last_action_ts = time.time() - 5.0
    mpc.last_setpoint, mpc.last_damper = 21.5, 0
    _, dmp, reason, _ = mpc.optimize(current_temp=21.0, current_co2=1300.0, current_occupancy=15,
                                     future_occupancy_schedule=[15] * 6)
    assert not reason.startswith("Dwell-time"), "CO2 above 1100 ppm must bypass the dwell filter"
    assert dmp >= 2


# ---------------------------------------------------------------- ML models

def test_model_01_forecasts_lecture_arrival(forecaster):
    tuesday_0800 = datetime(2026, 10, 6, 8, 0, tzinfo=STOCKHOLM)
    preds = forecaster.predict_horizon(current_time=tuesday_0800, current_occupancy=0, horizon_steps=6, step_dt_sec=300)
    assert len(preds) == 6 and all(0 <= p <= 50 for p in preds)
    assert preds[0] <= 3, f"08:05 is before the 08:15 lecture; got {preds}"
    assert max(preds[3:]) >= 5, f"Model 01 should anticipate the 08:15 lecture; got {preds}"

    sunday_night = datetime(2026, 10, 4, 23, 0, tzinfo=STOCKHOLM)
    assert max(forecaster.predict_horizon(sunday_night, 0)) <= 1


def test_model_02_matches_room_physics(predictor):
    rng = np.random.default_rng(1)
    n = 300
    t0 = rng.uniform(16, 25, n)
    c0 = rng.uniform(420, 1400, n)
    sp = rng.choice([18.0, 19.5, 20.5, 21.5, 22.5], n)
    d = rng.integers(0, 4, n).astype(float)
    occ = rng.choice([0, 0, 2, 5, 10, 15], n).astype(float)
    pred_t, pred_c, _ = predictor.predict_batch(t0, c0, sp, d, occ, 300.0)
    true_t, true_c = t0.copy(), c0.copy()
    for _ in range(300):
        true_t, true_c, _ = phys.euler_tick(true_t, true_c, sp, d, occ)
    assert np.mean(np.abs(pred_t - true_t)) < 0.3, "Model 02 temperature error too large"
    assert np.mean(np.abs(pred_c - true_c)) < 40.0, "Model 02 CO2 error too large"


def test_model_02_power_from_energy_balance(predictor):
    _, _, p = predictor.predict_step(temp=18.0, co2=420.0, setpoint=18.0, damper=0, occupancy=0, dt_seconds=300)
    assert p == pytest.approx(25 + 0.005 * 6 * 8750, rel=0.1)


# ---------------------------------------------------------------- safety & baseline

def test_staleness_detection():
    now = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(seconds=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    old = (now - timedelta(seconds=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert not safety.is_stale(safety.telemetry_age_seconds(fresh, now))
    assert safety.is_stale(safety.telemetry_age_seconds(old, now))
    assert safety.is_stale(safety.telemetry_age_seconds(None, now))
    cmd = safety.safe_fallback_command(45.0)
    assert (cmd["setpoint"], cmd["damper"]) == (21.0, 1)
    assert "SAFE FALLBACK" in cmd["reason"]


def test_baseline_uses_local_time_and_counts_comfort_only_when_occupied(dynamics):
    # 05:30 UTC is 07:30 in Stockholm (CEST): the schedule is already in comfort mode.
    assert fixed_schedule_targets(datetime(2026, 10, 6, 5, 30, tzinfo=timezone.utc)) == (22.0, 2)
    assert fixed_schedule_targets(datetime(2026, 10, 6, 21, 30, tzinfo=timezone.utc)) == (16.0, 0)

    base = BaselineController(dynamics)
    night = datetime(2026, 10, 6, 2, 0, tzinfo=STOCKHOLM)
    for _ in range(30):
        base.step(occupancy=0, dt_seconds=10, now=night)
    assert base.comfort_violations_deg_sec == 0.0, "An empty room at night is not a comfort violation"
    assert base.cumulative_energy_kwh > 0
