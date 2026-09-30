"""
Model 02: Thermal Response & Dynamics Machine Learning Model
Data-driven polynomial regression model trained on historical sensor telemetry
and thermodynamic response curves. Predicts future temperature, CO2 dilution,
and electrical power consumption for candidate HVAC actuation states.
Adheres to D7065E Proposal Specifications (Weeks 6-7 Deliverable).
"""

import os
import math
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Tuple, Dict, Any, List
import numpy as np

try:
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import PolynomialFeatures, StandardScaler
    from sklearn.pipeline import Pipeline
    from sklearn.metrics import mean_squared_error, r2_score
    import joblib
except ImportError:
    Ridge = None
    PolynomialFeatures = None
    StandardScaler = None
    Pipeline = None
    mean_squared_error = None
    r2_score = None
    joblib = None

logger = logging.getLogger("ThermalPredictor")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "thermal_model.joblib")
DB_PATH = os.getenv("DB_PATH", "/data/db/hvac.db")
if not os.path.exists(DB_PATH) and os.path.exists("data/db/hvac.db"):
    DB_PATH = "data/db/hvac.db"

class ThermalPredictor:
    def __init__(self, 
                 ambient_temp: float = 12.0, 
                 ambient_co2: float = 420.0,
                 model_file: str = MODEL_PATH):
        self.ambient_temp = ambient_temp
        self.ambient_co2 = ambient_co2
        self.model_file = model_file
        self.temp_pipeline = None
        self.co2_pipeline = None
        self.metrics = {"r2_score": 0.0, "rmse": 0.0, "samples": 0, "trained_at": None}
        self.feature_names = [
            "curr_temp",
            "ambient_temp",
            "setpoint",
            "occupancy",
            "damper",
            "temp_lift",       # (setpoint - curr_temp)
            "envelope_delta",   # (ambient_temp - curr_temp)
            "dt_sec"
        ]
        self.load_or_train()

    def _extract_features(self, temp: float, setpoint: float, occupancy: int, damper: int, dt_sec: float) -> List[float]:
        temp_lift = setpoint - temp
        envelope_delta = self.ambient_temp - temp
        return [temp, self.ambient_temp, setpoint, float(occupancy), float(damper), temp_lift, envelope_delta, dt_sec]

    def generate_synthetic_corpus(self, n_samples: int = 12000) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Generate a broad multi-condition operational corpus spanning all thermal regimes:
        - Temperatures: 15.0°C to 28.0°C
        - Setpoints: 16.0°C to 28.0°C
        - Occupancy: 0 to 30 people
        - Damper: 0 to 3
        - Ambients: 5.0°C to 20.0°C
        - dt: 10s to 300s
        """
        X = []
        y_temp = []
        y_co2 = []

        np.random.seed(42)
        for _ in range(n_samples):
            t = np.random.uniform(16.0, 27.0)
            c = np.random.uniform(420.0, 1600.0)
            sp = np.random.choice([16.0, 18.0, 19.5, 20.5, 21.0, 22.0, 24.0, 26.0, 28.0])
            dmp = np.random.choice([0, 1, 2, 3])
            occ = np.random.choice([0, 0, 0, 1, 2, 5, 8, 12, 18, 25])
            dt = np.random.choice([10.0, 30.0, 60.0, 120.0, 300.0])

            # Analytical thermodynamic differential basis
            rate_hvac = 0.04 * (sp - t)
            rate_env = 0.005 * (self.ambient_temp - t)
            rate_occ = 0.03 * float(occ)
            delta_t = (rate_hvac + rate_env + rate_occ) * (dt / 5.0)

            # Add stochastic thermal noise (0.02°C variance)
            noise_t = np.random.normal(0, 0.02)
            new_t = t + delta_t + noise_t

            # CO2 mass balance basis
            vent_rate = 0.01 + (0.02 * float(dmp))
            d_co2_gen = 1.5 * float(occ) * (dt / 5.0)
            d_co2_vent = vent_rate * (c - self.ambient_co2) * (dt / 5.0)
            noise_c = np.random.normal(0, 2.0)
            new_c = max(self.ambient_co2, c + (d_co2_gen - d_co2_vent) + noise_c)

            features = self._extract_features(t, sp, occ, dmp, dt)
            X.append(features)
            y_temp.append(new_t - t) # Predict delta_T
            y_co2.append(new_c - c)   # Predict delta_CO2

        return np.array(X), np.array(y_temp), np.array(y_co2)

    def train_from_telemetry_and_synthetic(self) -> Dict[str, Any]:
        """
        Train the polynomial Ridge regression model using logged database telemetry
        supplemented with the multi-regime synthetic dataset.
        """
        if Ridge is None:
            logger.warning("scikit-learn not available, using analytical forward equations.")
            return self.metrics

        logger.info("Training Model 02 (Polynomial Ridge Thermal Predictor)...")
        X, y_temp, y_co2 = self.generate_synthetic_corpus(n_samples=15000)

        # Attempt to inject actual historical telemetry observations if DB accessible
        if os.path.exists(DB_PATH):
            try:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT temperature, co2, occupancy, power_w, timestamp 
                    FROM telemetry 
                    ORDER BY timestamp ASC 
                    LIMIT 5000
                """)
                rows = cursor.fetchall()
                conn.close()

                if len(rows) > 10:
                    real_X = []
                    real_y_t = []
                    real_y_c = []
                    for i in range(len(rows) - 1):
                        t0, c0, occ0, p0, _ = rows[i]
                        t1, c1, _, _, _ = rows[i+1]
                        dt = 2.0 # 2-second gateway interval
                        # Back-calculate effective setpoint from power
                        sp_eff = t0 + (p0 - 25.0 - 45.0) / 350.0 if p0 > 70.0 else t0
                        dmp_eff = 1
                        real_X.append(self._extract_features(t0, sp_eff, occ0, dmp_eff, dt))
                        real_y_t.append(t1 - t0)
                        real_y_c.append(c1 - c0)

                    X = np.vstack([X, np.array(real_X)])
                    y_temp = np.concatenate([y_temp, np.array(real_y_t)])
                    y_co2 = np.concatenate([y_co2, np.array(real_y_c)])
                    logger.info(f"Incorporated {len(real_X)} historical observations from {DB_PATH}")
            except Exception as e:
                logger.warning(f"Could not load telemetry rows for training: {e}")

        # Train 80/20 train/test split
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y_temp[:split_idx], y_temp[split_idx:]

        # Polynomial Ridge pipeline for nonlinear bilinear thermal coupling
        pipe = Pipeline([
            ('scaler', StandardScaler()),
            ('poly', PolynomialFeatures(degree=2, include_bias=False)),
            ('ridge', Ridge(alpha=1.0))
        ])
        pipe.fit(X_train, y_train)

        preds = pipe.predict(X_test)
        r2 = float(r2_score(y_test, preds))
        rmse = float(math.sqrt(mean_squared_error(y_test, preds)))

        self.temp_pipeline = pipe

        # Train companion CO2 pipeline
        co2_pipe = Pipeline([
            ('scaler', StandardScaler()),
            ('poly', PolynomialFeatures(degree=2, include_bias=False)),
            ('ridge', Ridge(alpha=1.0))
        ])
        co2_pipe.fit(X_train, y_co2[:split_idx])
        self.co2_pipeline = co2_pipe

        self.metrics = {
            "algorithm": "Polynomial Ridge Regression (degree=2, alpha=1.0)",
            "r2_score": round(r2, 4),
            "rmse": round(rmse, 4),
            "samples": len(X),
            "trained_at": datetime.now(timezone.utc).isoformat()
        }

        try:
            joblib.dump({"temp": self.temp_pipeline, "co2": self.co2_pipeline, "metrics": self.metrics}, self.model_file)
            logger.info(f"Model 02 trained successfully! R2: {r2:.4f}, RMSE: {rmse:.4f}. Saved to {self.model_file}")
        except Exception as e:
            logger.warning(f"Could not persist model file: {e}")

        return self.metrics

    def load_or_train(self):
        """Load trained models from disk or fit fresh models."""
        if joblib and os.path.exists(self.model_file):
            try:
                bundle = joblib.load(self.model_file)
                self.temp_pipeline = bundle.get("temp")
                self.co2_pipeline = bundle.get("co2")
                self.metrics = bundle.get("metrics", self.metrics)
                logger.info(f"Loaded existing Model 02 from {self.model_file}")
                return
            except Exception as e:
                logger.warning(f"Failed to load {self.model_file}: {e}. Retraining...")
        self.train_from_telemetry_and_synthetic()

    def predict_step(self, 
                     temp: float, 
                     co2: float, 
                     setpoint: float, 
                     damper: int, 
                     occupancy: int, 
                     dt_seconds: float = 60.0) -> Tuple[float, float, float]:
        """
        Use the trained ML model to predict future room state over dt_seconds.
        Returns: (predicted_temp, predicted_co2, instantaneous_power_watts)
        """
        if self.temp_pipeline is not None and self.co2_pipeline is not None:
            try:
                features = np.array([self._extract_features(temp, setpoint, occupancy, damper, dt_seconds)])
                delta_t = float(self.temp_pipeline.predict(features)[0])
                delta_c = float(self.co2_pipeline.predict(features)[0])

                predicted_temp = temp + delta_t
                predicted_co2 = max(self.ambient_co2, co2 + delta_c)
            except Exception as e:
                logger.error(f"Prediction inference error: {e}. Falling back to physics equations.")
                predicted_temp, predicted_co2 = self._analytical_step(temp, co2, setpoint, damper, occupancy, dt_seconds)
        else:
            predicted_temp, predicted_co2 = self._analytical_step(temp, co2, setpoint, damper, occupancy, dt_seconds)

        # Calculate electrical power draw (Watts)
        base_power = 25.0
        fan_power = float(damper) * 45.0
        temp_lift = setpoint - predicted_temp
        heating_power = max(0.0, min(2500.0, temp_lift * 350.0))
        power_w = base_power + fan_power + heating_power

        return predicted_temp, predicted_co2, power_w

    def _analytical_step(self, temp: float, co2: float, setpoint: float, damper: int, occupancy: int, dt: float) -> Tuple[float, float]:
        rate_hvac = 0.04 * (setpoint - temp)
        rate_env = 0.005 * (self.ambient_temp - temp)
        rate_occ = 0.03 * float(occupancy)
        d_t = (rate_hvac + rate_env + rate_occ) * (dt / 5.0)

        vent_rate = 0.01 + (0.02 * float(damper))
        d_co2_gen = 1.5 * float(occupancy) * (dt / 5.0)
        d_co2_vent = vent_rate * (co2 - self.ambient_co2) * (dt / 5.0)

        return temp + d_t, max(self.ambient_co2, co2 + (d_co2_gen - d_co2_vent))

    def comfort_penalty(self, temp: float, co2: float) -> Tuple[float, float]:
        """Calculate ASHRAE 55 and IAQ comfort penalties."""
        temp_penalty = 0.0
        if temp < 20.0:
            temp_penalty = (20.0 - temp) ** 2
        elif temp > 24.0:
            temp_penalty = (temp - 24.0) ** 2

        co2_penalty = 0.0
        if co2 > 1000.0:
            co2_penalty = ((co2 - 1000.0) / 100.0) ** 2

        return temp_penalty, co2_penalty

