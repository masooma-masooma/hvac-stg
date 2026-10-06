"""
Model 02: Thermal & IAQ Response Predictor
Gradient-boosted regression trees on physics-informed features. Predicts the
temperature change and CO2 change of Room A109 over a horizon step for a
candidate (setpoint, damper) action, given the current state and occupancy.

Training data:
- Logged telemetry from the ingestor (/api/history), which stores the measured
  temperature, CO2 and occupancy together with the live setpoint and damper.
  State transitions are extracted at 10/30/60 s lags inside windows where the
  actuators and occupancy were constant.
- A synthetic corpus generated from the room physics (models/dynamics.py), used
  to cover regimes that rarely occur in the logs (e.g. 300 s steps, extreme
  setpoints). Its share is reported in the metrics.

Electrical power is not learned: it is derived from the predicted trajectory by
an energy balance (heater = dT/dt + losses - occupant gains).
"""

import os
import math
import logging
from datetime import datetime, timezone
from typing import Tuple, Dict, Any, List, Optional

import numpy as np
import requests

from models import dynamics as phys

try:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.metrics import mean_squared_error, r2_score
    import joblib
except ImportError:
    HistGradientBoostingRegressor = None
    mean_squared_error = None
    r2_score = None
    joblib = None

logger = logging.getLogger("ThermalPredictor")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "thermal_model.joblib")
MODEL_VERSION = 3
SYNTHETIC_DTS = (10.0, 30.0, 60.0, 120.0, 300.0)
TELEMETRY_LAGS = (10.0, 30.0, 60.0)
HEATING_TIME_CONSTANT_S = 1.0 / phys.K_HVAC


def fetch_training_telemetry(ingestor_url: str, room: str = "A109", minutes: int = 360) -> List[Dict[str, Any]]:
    """Fetch logged telemetry (with actuator state) from the ingestor REST API."""
    resp = requests.get(f"{ingestor_url}/api/history", params={"room": room, "minutes": minutes}, timeout=15)
    resp.raise_for_status()
    return resp.json() or []


class ThermalPredictor:
    def __init__(self,
                 ambient_temp: float = phys.AMBIENT_TEMP,
                 ambient_co2: float = phys.AMBIENT_CO2,
                 model_file: str = MODEL_PATH,
                 telemetry_rows: Optional[List[Dict[str, Any]]] = None):
        self.ambient_temp = ambient_temp
        self.ambient_co2 = ambient_co2
        self.model_file = model_file
        self.temp_model = None
        self.co2_model = None
        self.metrics: Dict[str, Any] = {"trained_at": None}
        self.load_or_train(telemetry_rows)

    def features(self, temp, co2, setpoint, damper, occupancy, dt_sec) -> np.ndarray:
        """Physics-informed feature matrix; every argument may be a scalar or an array."""
        temp, co2, setpoint, damper, occupancy, dt_sec = np.broadcast_arrays(
            np.asarray(temp, dtype=float), np.asarray(co2, dtype=float),
            np.asarray(setpoint, dtype=float), np.asarray(damper, dtype=float),
            np.asarray(occupancy, dtype=float), np.asarray(dt_sec, dtype=float))
        envelope = temp - self.ambient_temp
        excess_co2 = co2 - self.ambient_co2
        return np.column_stack([
            temp, co2, setpoint, damper, occupancy,
            setpoint - temp,          # heating lift
            envelope,                 # conductive loss driver
            damper * envelope,        # ventilation heat loss driver
            damper * excess_co2,      # ventilation CO2 removal driver
            excess_co2,
            dt_sec,
        ])

    def generate_synthetic_corpus(self, n_per_dt: int = 4000, seed: int = 42):
        """Simulate random states forward with the room physics for each step length."""
        rng = np.random.default_rng(seed)
        X, y_t, y_c = [], [], []
        for dt in SYNTHETIC_DTS:
            t0 = rng.uniform(14.0, 28.0, n_per_dt)
            c0 = rng.uniform(420.0, 1800.0, n_per_dt)
            sp = rng.choice([16.0, 18.0, 19.0, 19.5, 20.0, 20.5, 21.0, 21.5, 22.0, 22.5, 23.0, 24.0, 26.0, 28.0], n_per_dt)
            dmp = rng.integers(0, 4, n_per_dt).astype(float)
            occ = rng.choice([0, 0, 0, 1, 2, 5, 8, 10, 12, 15, 20, 25], n_per_dt).astype(float)
            t, c = t0.copy(), c0.copy()
            for _ in range(int(dt)):
                t, c, _ = phys.euler_tick(t, c, sp, dmp, occ, self.ambient_temp, self.ambient_co2)
            t = t + rng.normal(0.0, 0.05, n_per_dt)      # sensor quantisation / noise
            c = c + rng.normal(0.0, 2.0, n_per_dt)
            X.append(self.features(t0, c0, sp, dmp, occ, dt))
            y_t.append(t - t0)
            y_c.append(c - c0)
        return np.vstack(X), np.concatenate(y_t), np.concatenate(y_c)

    def telemetry_corpus(self, rows: List[Dict[str, Any]]):
        """Build (state, action) -> state transitions from logged telemetry."""
        usable = [r for r in rows if r.get("setpoint") is not None and r.get("damper") is not None]
        if len(usable) < 20:
            return None
        ts = np.array([datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp() for r in usable])
        order = np.argsort(ts, kind="stable")
        ts = ts[order]
        temp = np.array([r["temperature"] for r in usable], dtype=float)[order]
        co2 = np.array([r["co2"] for r in usable], dtype=float)[order]
        occ = np.array([r["occupancy"] for r in usable], dtype=float)[order]
        sp = np.array([r["setpoint"] for r in usable], dtype=float)[order]
        dmp = np.array([r["damper"] for r in usable], dtype=float)[order]

        # Segment id increments whenever an input changes or the log has a gap.
        changed = np.zeros(len(ts), dtype=bool)
        changed[1:] = (np.diff(sp) != 0) | (np.diff(dmp) != 0) | (np.diff(occ) != 0) | (np.diff(ts) > 5.0)
        segment = np.cumsum(changed)

        X, y_t, y_c = [], [], []
        for lag in TELEMETRY_LAGS:
            j = np.searchsorted(ts, ts + lag)
            valid = j < len(ts)
            i_idx = np.nonzero(valid)[0]
            j_idx = j[valid]
            keep = (np.abs(ts[j_idx] - ts[i_idx] - lag) <= 1.0) & (segment[j_idx] == segment[i_idx])
            i_idx, j_idx = i_idx[keep], j_idx[keep]
            if len(i_idx) == 0:
                continue
            X.append(self.features(temp[i_idx], co2[i_idx], sp[i_idx], dmp[i_idx], occ[i_idx], ts[j_idx] - ts[i_idx]))
            y_t.append(temp[j_idx] - temp[i_idx])
            y_c.append(co2[j_idx] - co2[i_idx])
        if not X:
            return None
        return np.vstack(X), np.concatenate(y_t), np.concatenate(y_c)

    def train(self, telemetry_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if HistGradientBoostingRegressor is None:
            logger.warning("scikit-learn not available, using the analytical physics model.")
            return self.metrics

        rng = np.random.default_rng(7)
        Xs, yts, ycs = self.generate_synthetic_corpus()
        is_real = np.zeros(len(Xs), dtype=bool)
        X, y_t, y_c = Xs, yts, ycs

        real = self.telemetry_corpus(telemetry_rows) if telemetry_rows else None
        n_real = 0
        if real is not None:
            Xr, ytr, ycr = real
            n_real = len(Xr)
            X = np.vstack([Xs, Xr])
            y_t = np.concatenate([yts, ytr])
            y_c = np.concatenate([ycs, ycr])
            is_real = np.concatenate([is_real, np.ones(n_real, dtype=bool)])

        idx = rng.permutation(len(X))
        split = int(0.8 * len(X))
        tr, te = idx[:split], idx[split:]

        def make():
            return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.1, max_leaf_nodes=31, random_state=0)

        temp_model = make().fit(X[tr], y_t[tr])
        co2_model = make().fit(X[tr], y_c[tr])
        pt, pc = temp_model.predict(X[te]), co2_model.predict(X[te])

        metrics = {
            "algorithm": "HistGradientBoostingRegressor x2 (dT, dCO2) on physics-informed features",
            "samples_total": int(len(X)),
            "samples_logged_telemetry": int(n_real),
            "samples_synthetic": int(len(Xs)),
            "test_r2_temp": round(float(r2_score(y_t[te], pt)), 4),
            "test_rmse_temp_c": round(float(math.sqrt(mean_squared_error(y_t[te], pt))), 4),
            "test_r2_co2": round(float(r2_score(y_c[te], pc)), 4),
            "test_rmse_co2_ppm": round(float(math.sqrt(mean_squared_error(y_c[te], pc))), 2),
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }
        real_te = te[is_real[te]]
        if len(real_te) >= 20:
            prt = temp_model.predict(X[real_te])
            metrics["test_rmse_temp_c_logged_only"] = round(float(math.sqrt(mean_squared_error(y_t[real_te], prt))), 4)
            metrics["test_samples_logged_only"] = int(len(real_te))

        self.temp_model, self.co2_model, self.metrics = temp_model, co2_model, metrics
        logger.info(f"Model 02 trained: {metrics}")
        if joblib:
            try:
                joblib.dump({"version": MODEL_VERSION, "temp": temp_model, "co2": co2_model, "metrics": metrics}, self.model_file)
            except Exception as e:
                logger.warning(f"Could not persist model file: {e}")
        return metrics

    def load_or_train(self, telemetry_rows=None):
        if joblib and os.path.exists(self.model_file):
            try:
                bundle = joblib.load(self.model_file)
                if bundle.get("version") == MODEL_VERSION:
                    self.temp_model = bundle["temp"]
                    self.co2_model = bundle["co2"]
                    self.metrics = bundle.get("metrics", self.metrics)
                    logger.info(f"Loaded existing Model 02 from {self.model_file}")
                    return
                logger.info("Model 02 file has an old feature version, retraining.")
            except Exception as e:
                logger.warning(f"Failed to load {self.model_file}: {e}. Retraining...")
        self.train(telemetry_rows)

    def predict_batch(self, temp, co2, setpoint, damper, occupancy, dt_seconds: float):
        """
        Vectorised prediction for many candidate actions at once.
        Returns arrays (next_temp, next_co2, average_power_w over the step).
        """
        temp = np.asarray(temp, dtype=float)
        co2 = np.asarray(co2, dtype=float)
        setpoint = np.asarray(setpoint, dtype=float)
        damper = np.asarray(damper, dtype=float)
        occupancy = np.asarray(occupancy, dtype=float)
        shape = np.broadcast(temp, co2, setpoint, damper, occupancy).shape

        if self.temp_model is not None and self.co2_model is not None:
            X = self.features(temp, co2, setpoint, damper, occupancy, dt_seconds)
            next_t = np.broadcast_to(temp, shape) + self.temp_model.predict(X).reshape(shape)
            next_c = np.maximum(self.ambient_co2, np.broadcast_to(co2, shape) + self.co2_model.predict(X).reshape(shape))
        else:
            next_t, next_c = np.broadcast_to(temp, shape).copy(), np.broadcast_to(co2, shape).copy()
            for _ in range(int(dt_seconds)):
                next_t, next_c, _ = phys.euler_tick(next_t, next_c, setpoint, damper, occupancy, self.ambient_temp, self.ambient_co2)

        # Energy balance over the step. The room approaches its new temperature with
        # time constant ~1/K_HVAC, so the average temperature sits close to next_t.
        settle = min(1.0, HEATING_TIME_CONSTANT_S / dt_seconds)
        t_avg = next_t - (next_t - temp) * settle
        loss = (phys.K_ENVELOPE + phys.K_VENT_THERMAL * damper) * (t_avg - self.ambient_temp)
        gain = phys.K_OCC_HEAT * occupancy
        heater = np.clip((next_t - temp) / dt_seconds + loss - gain, 0.0, phys.MAX_HEATER_RATE)
        power = phys.BASE_W + phys.FAN_W_PER_LEVEL * damper + phys.W_PER_RATE * heater
        return next_t, next_c, np.broadcast_to(power, shape)

    def predict_step(self, temp: float, co2: float, setpoint: float, damper: int,
                     occupancy: int, dt_seconds: float = 60.0) -> Tuple[float, float, float]:
        t, c, p = self.predict_batch(temp, co2, setpoint, damper, occupancy, dt_seconds)
        return float(t), float(c), float(p)

    def comfort_penalty(self, temp: float, co2: float) -> Tuple[float, float]:
        return phys.BuildingDynamics().comfort_penalty(temp, co2)
