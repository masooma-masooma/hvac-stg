"""
Smart HVAC Optimization Controller Service
Runs the receding-horizon MPC loop (Model 01 + Model 02), the fixed-schedule
baseline twin, telemetry staleness supervision (Safe Fallback Mode), and exposes
REST endpoints plus a WebSocket push stream (/ws/telemetry) on port 8082.
"""

import os
import time
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from models.dynamics import BuildingDynamics
from models.occupancy_forecaster import OccupancyForecaster
from models.thermal_model import ThermalPredictor, fetch_training_telemetry
from controller.mpc import MPCOptimizer
from controller import safety
from comparator.baseline import BaselineController

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SmartController")

INGESTOR_URL = os.getenv("INGESTOR_URL", "http://telemetry-ingestor:8081")
ACTUATOR_URL = os.getenv("ACTUATOR_URL", "http://actuator-controller:8080")
CONTROL_INTERVAL_SEC = float(os.getenv("CONTROL_INTERVAL", "10.0"))
TARGET_ROOM = os.getenv("TARGET_ROOM", "A109")
WS_PUSH_INTERVAL_SEC = float(os.getenv("WS_PUSH_INTERVAL", "1.0"))


def _initial_telemetry() -> Optional[List[Dict[str, Any]]]:
    try:
        return fetch_training_telemetry(INGESTOR_URL, TARGET_ROOM)
    except Exception as e:
        logger.info(f"No logged telemetry available for Model 02 training yet ({e}); using synthetic corpus only.")
        return None


dynamics = BuildingDynamics()
forecaster = OccupancyForecaster()
thermal_predictor = ThermalPredictor(telemetry_rows=_initial_telemetry())
optimizer = MPCOptimizer(dynamics=dynamics, occupancy_forecaster=forecaster, thermal_predictor=thermal_predictor,
                         horizon_steps=6, step_dt_seconds=300.0, dwell_time_seconds=2 * CONTROL_INTERVAL_SEC)
baseline = BaselineController(dynamics=dynamics)

latest_state: Dict[str, Any] = {
    "temperature": None, "co2": None, "occupancy": 0, "power_w": None,
    "setpoint": None, "damper": None, "timestamp": None, "age_s": None,
}
latest_plan: Dict[str, Any] = {
    "mode": "STARTING", "setpoint": None, "damper": None,
    "reason": "Initializing...", "trajectory": [], "forecast": [], "dispatch_ok": None,
    "updated_at": None,
}

smart_cumulative_energy_kwh = 0.0
smart_comfort_violation_deg_sec = 0.0
smart_co2_violation_ppm_sec = 0.0
last_step_time = time.time()


def fetch_recent_telemetry() -> Optional[List[Dict[str, Any]]]:
    """Recent telemetry rows from the ingestor; None if the pipeline is unreachable."""
    try:
        resp = requests.get(f"{INGESTOR_URL}/api/history", params={"room": TARGET_ROOM, "minutes": 2}, timeout=3)
        if resp.status_code == 200:
            return resp.json() or []
        logger.warning(f"Ingestor history returned HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"Ingestor fetch error: {e}")
    return None


def dispatch_actuation(setpoint: float, damper: int, reason: str) -> bool:
    try:
        payload = {"room": TARGET_ROOM, "setpoint": setpoint, "damper": damper, "reason": reason}
        res = requests.post(f"{ACTUATOR_URL}/commands", json=payload, timeout=3)
        if res.status_code == 200:
            logger.info(f"Actuation dispatched -> Setpoint: {setpoint}°C, Damper: {damper} ({reason})")
            return True
        logger.warning(f"Actuation dispatch returned status {res.status_code}: {res.text}")
    except Exception as e:
        logger.error(f"Failed to dispatch actuation command: {e}")
    return False


def mean_power_since(records: List[Dict[str, Any]], since_epoch: float) -> Optional[float]:
    values = []
    for r in records:
        ts = safety.parse_timestamp(r.get("timestamp", ""))
        if ts is not None and ts.timestamp() >= since_epoch and r.get("power_w") is not None:
            values.append(float(r["power_w"]))
    return sum(values) / len(values) if values else None


def control_step():
    """One sense-reason-act cycle (blocking; runs in a worker thread)."""
    global smart_cumulative_energy_kwh, smart_comfort_violation_deg_sec, smart_co2_violation_ppm_sec, last_step_time

    now = time.time()
    dt = min(now - last_step_time, 6 * CONTROL_INTERVAL_SEC)
    window_start = last_step_time
    last_step_time = now

    records = fetch_recent_telemetry()
    latest = records[-1] if records else None
    age = safety.telemetry_age_seconds(latest.get("timestamp") if latest else None)
    latest_state["age_s"] = None if age == float("inf") else round(age, 1)

    if safety.is_stale(age):
        cmd = safety.safe_fallback_command(age)
        ok = dispatch_actuation(cmd["setpoint"], cmd["damper"], cmd["reason"])
        optimizer.force_state(cmd["setpoint"], cmd["damper"])
        latest_plan.update(mode="SAFE_FALLBACK", setpoint=cmd["setpoint"], damper=cmd["damper"],
                           reason=cmd["reason"], trajectory=[], forecast=[], dispatch_ok=ok,
                           updated_at=datetime.now(timezone.utc).isoformat())
        logger.warning(cmd["reason"])
        return

    for key in ("temperature", "co2", "occupancy", "power_w", "setpoint", "damper", "timestamp"):
        latest_state[key] = latest.get(key)
    curr_temp = float(latest["temperature"])
    curr_co2 = float(latest["co2"])
    curr_occ = int(latest["occupancy"])

    # Energy: integrate the measured power over this cycle; the baseline twin sees the same occupancy.
    power = mean_power_since(records, window_start)
    if power is None:
        power = float(latest.get("power_w") or 0.0)
    smart_cumulative_energy_kwh += power * dt / 3600000.0
    baseline.step(occupancy=curr_occ, dt_seconds=dt)

    if curr_occ > 0:
        t_pen, c_pen = dynamics.comfort_penalty(curr_temp, curr_co2)
        smart_comfort_violation_deg_sec += (t_pen ** 0.5) * dt
        smart_co2_violation_ppm_sec += (c_pen ** 0.5) * 100.0 * dt

    opt_sp, opt_dmp, reason, trajectory = optimizer.optimize(
        current_temp=curr_temp, current_co2=curr_co2, current_occupancy=curr_occ)

    suppressed = reason.startswith("Dwell-time guardrail")
    ok = None if suppressed else dispatch_actuation(opt_sp, opt_dmp, reason)
    latest_plan.update(mode="MPC", setpoint=opt_sp, damper=opt_dmp, reason=reason, trajectory=trajectory,
                       forecast=list(optimizer.last_forecast), dispatch_ok=ok,
                       updated_at=datetime.now(timezone.utc).isoformat())

    savings = savings_pct()
    logger.info(f"Loop step | {curr_temp:.1f}°C {curr_co2:.0f}ppm {curr_occ}p | Power: {power:.0f}W | "
                f"Baseline kWh: {baseline.cumulative_energy_kwh:.4f} | Smart kWh: {smart_cumulative_energy_kwh:.4f} | "
                f"Saved: {savings:.1f}%")


def savings_pct() -> float:
    if baseline.cumulative_energy_kwh <= 0:
        return 0.0
    return (baseline.cumulative_energy_kwh - smart_cumulative_energy_kwh) / baseline.cumulative_energy_kwh * 100.0


async def control_loop():
    logger.info("Starting Autonomous MPC Control Loop...")
    await asyncio.sleep(5)
    global last_step_time
    last_step_time = time.time()
    while True:
        started = time.time()
        try:
            await asyncio.to_thread(control_step)
        except Exception as e:
            logger.error(f"Error in MPC control loop step: {e}", exc_info=True)
        await asyncio.sleep(max(0.5, CONTROL_INTERVAL_SEC - (time.time() - started)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(control_loop())
    yield
    task.cancel()


app = FastAPI(title="Smart HVAC MPC Optimizer & Baseline Comparator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _round(value, digits):
    return None if value is None else round(float(value), digits)


@app.get("/healthz")
def health():
    return {"status": "UP", "service": "smart-controller", "mode": latest_plan["mode"],
            "telemetry_age_s": latest_state["age_s"]}


@app.get("/api/comparison")
def get_comparison():
    savings_kwh = baseline.cumulative_energy_kwh - smart_cumulative_energy_kwh
    return {
        "room": TARGET_ROOM,
        "mode": latest_plan["mode"],
        "telemetry_age_s": latest_state["age_s"],
        "smart_controller": {
            "mode": "Model Predictive Control (MPC)" if latest_plan["mode"] == "MPC" else latest_plan["mode"],
            "current_temp": _round(latest_state["temperature"], 2),
            "current_co2": _round(latest_state["co2"], 0),
            "current_occupancy": latest_state["occupancy"],
            "current_power_watts": _round(latest_state["power_w"], 1),
            "applied_setpoint": latest_state["setpoint"],
            "applied_damper": latest_state["damper"],
            "cumulative_energy_kwh": round(smart_cumulative_energy_kwh, 5),
            "comfort_violation_degree_mins": round(smart_comfort_violation_deg_sec / 60.0, 2),
            "co2_violation_ppm_mins": round(smart_co2_violation_ppm_sec / 60.0, 2),
            "current_action": {
                "setpoint": latest_plan["setpoint"],
                "damper": latest_plan["damper"],
                "reason": latest_plan["reason"],
                "dispatch_ok": latest_plan["dispatch_ok"],
            },
        },
        "baseline_controller": {
            "mode": "Fixed schedule thermostat (22C/damper 2 06-22, 16C/damper 0 at night)",
            "simulated_temp": round(baseline.temp, 2),
            "simulated_co2": round(baseline.co2, 0),
            "setpoint": baseline.current_setpoint,
            "damper": baseline.current_damper,
            "power_watts": round(baseline.last_power_w, 1),
            "cumulative_energy_kwh": round(baseline.cumulative_energy_kwh, 5),
            "comfort_violation_degree_mins": round(baseline.comfort_violations_deg_sec / 60.0, 2),
            "co2_violation_ppm_mins": round(baseline.co2_violations_ppm_sec / 60.0, 2),
        },
        "benchmarks": {
            "energy_saved_kwh": round(savings_kwh, 5),
            "energy_savings_percentage": round(savings_pct(), 1),
            "comfort_preserved": smart_comfort_violation_deg_sec <= baseline.comfort_violations_deg_sec + 1e-9,
        },
    }


@app.post("/api/benchmarks/reset")
def reset_benchmarks():
    global smart_cumulative_energy_kwh, smart_comfort_violation_deg_sec, smart_co2_violation_ppm_sec
    smart_cumulative_energy_kwh = 0.0
    smart_comfort_violation_deg_sec = 0.0
    smart_co2_violation_ppm_sec = 0.0
    baseline.reset(temp=latest_state["temperature"], co2=latest_state["co2"])
    return {"status": "reset", "message": "Cumulative energy and violation counters reset; baseline twin re-synchronised."}


@app.get("/api/mpc/trajectory")
def get_trajectory():
    return {
        "room": TARGET_ROOM,
        "horizon_steps": optimizer.horizon,
        "step_duration_minutes": int(optimizer.step_dt / 60.0),
        "move_blocks": list(optimizer.last_blocks),
        "latest_plan": latest_plan,
    }


@app.get("/api/models")
def get_models():
    return {"model_01_occupancy_forecaster": forecaster.metrics,
            "model_02_thermal_predictor": thermal_predictor.metrics}


@app.post("/api/models/retrain")
def retrain_models(minutes: int = 360):
    """Retrain Model 02 on the last `minutes` of logged telemetry (plus the synthetic corpus)."""
    rows = fetch_training_telemetry(INGESTOR_URL, TARGET_ROOM, minutes=minutes)
    metrics = thermal_predictor.train(rows)
    return {"status": "retrained", "model_02_thermal_predictor": metrics}


@app.websocket("/ws/telemetry")
async def telemetry_stream(websocket: WebSocket):
    """Push the comparison snapshot and current MPC plan to the dashboard every second."""
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({"type": "snapshot",
                                       "sent_at": datetime.now(timezone.utc).isoformat(),
                                       "comparison": get_comparison(),
                                       "trajectory": get_trajectory()})
            await asyncio.sleep(WS_PUSH_INTERVAL_SEC)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.info(f"WebSocket client dropped: {e}")
