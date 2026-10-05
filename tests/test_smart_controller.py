"""
Unit Tests for Smart HVAC Optimization Controller
Tests:
1. BuildingDynamics.step (Thermodynamic heat balance, CO2 mass balance, power model)
2. MPCOptimizer.optimize (Occupied vs. Empty room policies, Dwell-time anti-chattering filter)
3. Model 01 (Random Forest Occupancy Forecaster)
4. Model 02 (Polynomial Ridge Thermal Predictor)
"""

import sys
import os
import time
from datetime import datetime, timezone
import pytest

# Add services/smart-controller to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "services", "smart-controller")))

from models.dynamics import BuildingDynamics
from models.occupancy_forecaster import OccupancyForecaster
from models.thermal_model import ThermalPredictor
from controller.mpc import MPCOptimizer

@pytest.fixture
def dynamics():
    return BuildingDynamics(ambient_temp=12.0, ambient_co2=420.0)

@pytest.fixture
def mpc(dynamics):
    forecaster = OccupancyForecaster()
    predictor = ThermalPredictor(ambient_temp=12.0, ambient_co2=420.0)
    return MPCOptimizer(
        dynamics=dynamics,
        occupancy_forecaster=forecaster,
        thermal_predictor=predictor,
        horizon_steps=6,
        step_dt_seconds=300.0,
        dwell_time_seconds=60.0
    )

def test_building_dynamics_heating_response(dynamics):
    """Verify that calling step with setpoint > temp causes temperature to rise"""
    initial_temp = 18.0
    setpoint = 24.0
    new_temp, new_co2, power_w = dynamics.step(
        temp=initial_temp,
        co2=450.0,
        setpoint=setpoint,
        damper=1,
        occupancy=0,
        dt_seconds=60.0
    )
    assert new_temp > initial_temp, f"Temperature should rise when heating; got {new_temp} <= {initial_temp}"
    assert power_w > 100.0, "Heating power should be active when setpoint > temp"

def test_building_dynamics_co2_occupancy_generation(dynamics):
    """Verify that occupants increase CO2, and higher damper increases ventilation dilution"""
    # 1. Occupied with closed damper
    _, co2_closed, _ = dynamics.step(temp=21.0, co2=500.0, setpoint=21.0, damper=0, occupancy=10, dt_seconds=60.0)
    assert co2_closed > 500.0, "CO2 must increase with 10 occupants"

    # 2. Occupied with open damper 3
    _, co2_vented, _ = dynamics.step(temp=21.0, co2=500.0, setpoint=21.0, damper=3, occupancy=10, dt_seconds=60.0)
    assert co2_vented < co2_closed, "CO2 with Damper 3 must be lower than Damper 0 due to mechanical dilution"

def test_building_dynamics_comfort_penalties(dynamics):
    """Verify ASHRAE 55 and IAQ penalty boundaries"""
    # Ideal comfort
    t_pen, c_pen = dynamics.comfort_penalty(temp=22.0, co2=600.0)
    assert t_pen == 0.0, "22°C is within 20-24°C ASHRAE band; penalty should be 0"
    assert c_pen == 0.0, "600 ppm is below 1000 ppm threshold; penalty should be 0"

    # Too cold & high CO2
    t_cold, c_high = dynamics.comfort_penalty(temp=18.0, co2=1200.0)
    assert t_cold > 0.0, "18°C is below 20°C; penalty must be positive"
    assert c_high > 0.0, "1200 ppm exceeds 1000 ppm ceiling; penalty must be positive"

def test_mpc_empty_room_eco_setback(mpc):
    """Verify that an empty room triggers an energy-saving eco-setback policy"""
    current_temp = 21.0
    current_co2 = 450.0
    occupancy = 0

    opt_sp, opt_dmp, reason, trajectory = mpc.optimize(
        current_temp=current_temp,
        current_co2=current_co2,
        current_occupancy=occupancy,
        future_occupancy_schedule=[0, 0, 0, 0, 0, 0]
    )

    assert len(trajectory) == 6, "Horizon trajectory must contain 6 steps"
    assert opt_sp <= 21.5, f"Empty room should choose an eco setback setpoint <= 21.5°C; got {opt_sp}"
    assert opt_dmp <= 1, f"Empty room should not run high ventilation fan speeds; got damper {opt_dmp}"
    assert "Eco setback" in reason or "conserving" in reason.lower()

def test_mpc_occupied_room_comfort_preservation(mpc):
    """Verify that an occupied room maintains ASHRAE comfort and increases ventilation"""
    current_temp = 19.5
    current_co2 = 950.0
    occupancy = 12

    opt_sp, opt_dmp, reason, trajectory = mpc.optimize(
        current_temp=current_temp,
        current_co2=current_co2,
        current_occupancy=occupancy,
        future_occupancy_schedule=[12, 12, 12, 12, 12, 12]
    )

    assert opt_dmp >= 2, f"Occupied room with CO2=950 ppm must run damper >= 2; got {opt_dmp}"
    assert opt_sp >= 19.5, f"Occupied room must maintain comfort setpoint; got {opt_sp}"

def test_mpc_dwell_time_filter(mpc):
    """Verify that the dwell-time anti-chattering filter suppresses rapid switching"""
    # 1. Apply initial change to set last_action_ts
    mpc.optimize(current_temp=21.0, current_co2=500.0, current_occupancy=0)
    mpc.last_action_ts = time.time() # Just acted right now
    mpc.last_setpoint = 20.5
    mpc.last_damper = 1

    # 2. Immediately call optimize with perturbed inputs (dwell time < 60s)
    sp, dmp, reason, _ = mpc.optimize(current_temp=21.2, current_co2=510.0, current_occupancy=0)

    # Must suppress chattering and retain last state
    assert "Dwell-time guardrail active" in reason, f"Should activate dwell filter; got: {reason}"
    assert sp == 20.5, f"Setpoint should remain at last setpoint 20.5; got {sp}"
    assert dmp == 1, f"Damper should remain at last damper 1; got {dmp}"

def test_model_01_occupancy_forecaster():
    """Verify Model 01 (Random Forest Occupancy Forecaster) output shape and bounds"""
    forecaster = OccupancyForecaster()
    now = datetime(2026, 10, 5, 9, 30, 0, tzinfo=timezone.utc)
    preds = forecaster.predict_horizon(current_time=now, current_occupancy=5, horizon_steps=6, step_dt_sec=300.0)

    assert len(preds) == 6, f"Expected 6 steps, got {len(preds)}"
    for p in preds:
        assert isinstance(p, int) or isinstance(p, float)
        assert p >= 0, f"Predicted occupancy cannot be negative; got {p}"
        assert p <= 50, f"Predicted occupancy exceeds room physical capacity; got {p}"

def test_model_02_thermal_predictor():
    """Verify Model 02 (Polynomial Ridge Thermal Predictor) inference step"""
    predictor = ThermalPredictor(ambient_temp=12.0, ambient_co2=420.0)
    t_next, c_next, p_w = predictor.predict_step(
        temp=20.0,
        co2=500.0,
        setpoint=22.0,
        damper=2,
        occupancy=5,
        dt_seconds=300.0
    )
    assert 16.0 <= t_next <= 28.0, f"Predicted temperature {t_next} out of physical bounds"
    assert c_next >= 420.0, f"Predicted CO2 {c_next} cannot drop below outdoor ambient 420"
    assert p_w >= 25.0, f"Power {p_w} must at least cover standby baseline"

