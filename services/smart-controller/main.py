"""
Smart HVAC Optimization Controller Service
Orchestrates the Model Predictive Control (MPC) optimization loop,
runs the parallel fixed-schedule baseline comparator, and exposes
benchmarking metrics via REST API on port 8082.
"""

import os
import time
import asyncio
import logging
import requests
from contextlib import asynccontextmanager
from typing import Dict, Any, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from models.dynamics import BuildingDynamics
from controller.mpc import MPCOptimizer
from comparator.baseline import BaselineController

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SmartController")

# Configuration from environment
INGESTOR_URL = os.getenv("INGESTOR_URL", "http://telemetry-ingestor:8081")
ACTUATOR_URL = os.getenv("ACTUATOR_URL", "http://actuator-controller:8080")
BUILDSIM_URL = os.getenv("BUILDSIM_URL", "http://buildsim:9090")
CONTROL_INTERVAL_SEC = float(os.getenv("CONTROL_INTERVAL", "10.0"))
TARGET_ROOM = os.getenv("TARGET_ROOM", "A109")

# Global instances
dynamics = BuildingDynamics(ambient_temp=12.0, ambient_co2=420.0)
optimizer = MPCOptimizer(dynamics=dynamics, horizon_steps=6, step_dt_seconds=300.0, dwell_time_seconds=60.0)
baseline = BaselineController(dynamics=dynamics)

# State cache
latest_state = {
    "temperature": 21.0,
    "co2": 450.0,
    "occupancy": 0,
    "power_w": 70.0,
    "last_update": 0.0
}

latest_plan = {
    "setpoint": 21.0,
    "damper": 1,
    "reason": "Initializing...",
    "trajectory": []
}

smart_cumulative_energy_kwh = 0.0
smart_comfort_violation_deg_sec = 0.0
smart_co2_violation_ppm_sec = 0.0
last_step_time = time.time()

def fetch_current_telemetry() -> Dict[str, Any]:
    """Fetch recent telemetry from Ingestor or BuildSim fallback"""
    global latest_state
    try:
        resp = requests.get(f"{INGESTOR_URL}/api/history?room={TARGET_ROOM}&minutes=2", timeout=3)
        if resp.status_code == 200:
            records = resp.json()
            if records and len(records) > 0:
                latest = records[-1]
                latest_state["temperature"] = latest.get("temperature", latest_state["temperature"])
                latest_state["co2"] = latest.get("co2", latest_state["co2"])
                latest_state["occupancy"] = latest.get("occupancy", latest_state["occupancy"])
                latest_state["power_w"] = latest.get("power_w", latest_state["power_w"])
                latest_state["last_update"] = time.time()
                return latest_state
    except Exception as e:
        logger.warning(f"Ingestor fetch error: {e}. Trying direct BuildSim sensors...")

    # Fallback directly to BuildSim
    try:
        t_resp = requests.get(f"{BUILDSIM_URL}/api/sensors/{TARGET_ROOM}-temp", timeout=2)
        c_resp = requests.get(f"{BUILDSIM_URL}/api/sensors/{TARGET_ROOM}-co2", timeout=2)
        o_resp = requests.get(f"{BUILDSIM_URL}/api/sensors/{TARGET_ROOM}-occ", timeout=2)
        if t_resp.status_code == 200:
            latest_state["temperature"] = float(t_resp.json().get("value", 21.0))
        if c_resp.status_code == 200:
            latest_state["co2"] = float(c_resp.json().get("value", 450.0))
        if o_resp.status_code == 200:
            latest_state["occupancy"] = int(float(o_resp.json().get("value", 0)))
        latest_state["last_update"] = time.time()
    except Exception as e2:
        logger.error(f"BuildSim fallback error: {e2}")

    return latest_state

def dispatch_actuation(setpoint: float, damper: int, reason: str):
    """Dispatch chosen control decisions to Actuator Controller"""
    try:
        payload = {
            "room": TARGET_ROOM,
            "setpoint": setpoint,
            "damper": damper,
            "reason": reason
        }
        res = requests.post(f"{ACTUATOR_URL}/commands", json=payload, timeout=3)
        if res.status_code == 200:
            logger.info(f"Actuation dispatched -> Setpoint: {setpoint}°C, Damper: {damper} ({reason})")
        else:
            logger.warning(f"Actuation dispatch returned status {res.status_code}: {res.text}")
    except Exception as e:
        logger.error(f"Failed to dispatch actuation command: {e}")

async def control_loop():
    """Continuous optimization loop running every CONTROL_INTERVAL_SEC seconds"""
    global smart_cumulative_energy_kwh, smart_comfort_violation_deg_sec, smart_co2_violation_ppm_sec, last_step_time, latest_plan

    logger.info("Starting Autonomous MPC Control Loop...")
    # Give containers a few seconds to settle
    await asyncio.sleep(5)

    while True:
        try:
            now = time.time()
            dt = now - last_step_time
            last_step_time = now

            # 1. Fetch live telemetry
            state = fetch_current_telemetry()
            curr_temp = state["temperature"]
            curr_co2 = state["co2"]
            curr_occ = state["occupancy"]
            curr_power = state["power_w"]

            # 2. Advance Baseline Virtual Comparator
            baseline_result = baseline.step(occupancy=curr_occ, dt_seconds=dt)

            # 3. Track Smart Controller actual energy & penalties
            smart_energy = (curr_power * dt) / 3600000.0
            smart_cumulative_energy_kwh += smart_energy

            t_pen, c_pen = dynamics.comfort_penalty(curr_temp, curr_co2)
            if t_pen > 0:
                smart_comfort_violation_deg_sec += (t_pen ** 0.5) * dt
            if c_pen > 0:
                smart_co2_violation_ppm_sec += (c_pen ** 0.5) * dt

            # 4. Solve MPC Optimization Problem
            opt_sp, opt_dmp, reason, trajectory = optimizer.optimize(
                current_temp=curr_temp,
                current_co2=curr_co2,
                current_occupancy=curr_occ
            )

            latest_plan["setpoint"] = opt_sp
            latest_plan["damper"] = opt_dmp
            latest_plan["reason"] = reason
            latest_plan["trajectory"] = trajectory

            # 5. Dispatch command to physical actuators if dwell filter permits
            if "Suppressing chattering" not in reason:
                dispatch_actuation(opt_sp, opt_dmp, reason)

            # Log benchmark summary
            savings_pct = 0.0
            if baseline.cumulative_energy_kwh > 0:
                savings_pct = ((baseline.cumulative_energy_kwh - smart_cumulative_energy_kwh) / baseline.cumulative_energy_kwh) * 100.0

            logger.info(f"Loop step | Smart: {curr_temp:.1f}°C | Power: {curr_power:.0f}W | Baseline kWh: {baseline.cumulative_energy_kwh:.4f} | Smart kWh: {smart_cumulative_energy_kwh:.4f} | Saved: {savings_pct:.1f}%")

        except Exception as e:
            logger.error(f"Error in MPC control loop step: {e}", exc_info=True)

        await asyncio.sleep(CONTROL_INTERVAL_SEC)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(control_loop())
    yield
    task.cancel()

app = FastAPI(title="Smart HVAC MPC Optimizer & Baseline Comparator", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/healthz")
def health():
    return {"status": "UP", "service": "smart-controller", "mode": "MPC Autonomous Optimizer"}

@app.get("/api/comparison")
def get_comparison():
    """Returns live comparative analytics between Smart MPC and Fixed Baseline"""
    savings_kwh = baseline.cumulative_energy_kwh - smart_cumulative_energy_kwh
    savings_pct = 0.0
    if baseline.cumulative_energy_kwh > 0:
        savings_pct = (savings_kwh / baseline.cumulative_energy_kwh) * 100.0

    return {
        "room": TARGET_ROOM,
        "smart_controller": {
            "mode": "Model Predictive Control (MPC)",
            "current_temp": round(latest_state["temperature"], 2),
            "current_co2": round(latest_state["co2"], 0),
            "current_occupancy": latest_state["occupancy"],
            "current_power_watts": round(latest_state["power_w"], 1),
            "cumulative_energy_kwh": round(smart_cumulative_energy_kwh, 5),
            "comfort_violation_degree_mins": round(smart_comfort_violation_deg_sec / 60.0, 2),
            "co2_violation_ppm_mins": round(smart_co2_violation_ppm_sec / 60.0, 2),
            "current_action": {
                "setpoint": latest_plan["setpoint"],
                "damper": latest_plan["damper"],
                "reason": latest_plan["reason"]
            }
        },
        "baseline_controller": {
            "mode": "Fixed Schedule Thermostat (ASHRAE 90.1 standard)",
            "simulated_temp": round(baseline.temp, 2),
            "simulated_co2": round(baseline.co2, 0),
            "setpoint": baseline.current_setpoint,
            "damper": baseline.current_damper,
            "power_watts": round(baseline.last_power_w, 1),
            "cumulative_energy_kwh": round(baseline.cumulative_energy_kwh, 5),
            "comfort_violation_degree_mins": round(baseline.comfort_violations_deg_sec / 60.0, 2),
            "co2_violation_ppm_mins": round(baseline.co2_violations_ppm_sec / 60.0, 2)
        },
        "benchmarks": {
            "energy_saved_kwh": round(savings_kwh, 5),
            "energy_savings_percentage": round(savings_pct, 1),
            "comfort_preserved": smart_comfort_violation_deg_sec <= baseline.comfort_violations_deg_sec
        }
    }

@app.post("/api/benchmarks/reset")
def reset_benchmarks():
    """Reset cumulative metrics for fresh benchmark testing"""
    global smart_cumulative_energy_kwh, smart_comfort_violation_deg_sec, smart_co2_violation_ppm_sec
    smart_cumulative_energy_kwh = 0.0
    smart_comfort_violation_deg_sec = 0.0
    smart_co2_violation_ppm_sec = 0.0
    baseline.reset()
    return {"status": "reset", "message": "Cumulative energy and violation counters reset to 0.0"}

@app.get("/api/mpc/trajectory")
def get_trajectory():
    """Returns the forecasted states over the lookahead horizon"""
    return {
        "room": TARGET_ROOM,
        "horizon_steps": optimizer.horizon,
        "step_duration_minutes": int(optimizer.step_dt / 60.0),
        "latest_plan": latest_plan
    }

