"""
Model 01: Occupancy Forecasting Machine Learning Model
Predicts per-zone future occupancy across lookahead horizons using Random Forest
regression trained on academic timetables, time-of-day, and real-time lag features.
Adheres to D7065E Proposal Specifications (Weeks 4-5 Deliverable).
"""

import os
import math
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple
import numpy as np

try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import mean_squared_error, r2_score
    import joblib
except ImportError:
    RandomForestRegressor = None
    mean_squared_error = None
    r2_score = None
    joblib = None

logger = logging.getLogger("OccupancyForecaster")

MODEL_PATH = os.path.join(os.path.dirname(__file__), "occupancy_forecaster.joblib")

class OccupancyForecaster:
    def __init__(self, model_file: str = MODEL_PATH):
        self.model_file = model_file
        self.model = None
        self.metrics = {"r2_score": 0.0, "rmse": 0.0, "trained_at": None, "samples": 0}
        self.feature_names = [
            "hour_float",
            "day_of_week",
            "is_weekend",
            "is_lecture_block",
            "is_lunch",
            "is_fika",
            "lead_time_min",
            "lag_occupancy"
        ]
        self.load_or_train()

    def _extract_features(self, dt: datetime, lead_time_min: float, lag_occ: int) -> List[float]:
        """Extract academic calendar and temporal features for a given timestamp."""
        future_dt = dt + timedelta(minutes=lead_time_min)
        hour = future_dt.hour + (future_dt.minute / 60.0)
        dow = future_dt.weekday() # 0 = Monday, 6 = Sunday
        is_weekend = 1.0 if dow >= 5 else 0.0

        # LTU Academic Schedule Patterns:
        # Lecture blocks: 08:15-10:00, 10:15-12:00, 13:15-15:00, 15:15-17:00
        is_lecture = 0.0
        if not is_weekend:
            if (8.25 <= hour <= 10.0) or (10.25 <= hour <= 12.0) or (13.25 <= hour <= 15.0) or (15.25 <= hour <= 17.0):
                is_lecture = 1.0

        # Lunch break: 12:00-13:00
        is_lunch = 1.0 if (not is_weekend and 12.0 <= hour < 13.0) else 0.0

        # Swedish Fika breaks: 09:45-10:15 and 14:45-15:15
        is_fika = 1.0 if (not is_weekend and ((9.75 <= hour <= 10.25) or (14.75 <= hour <= 15.25))) else 0.0

        return [hour, float(dow), is_weekend, is_lecture, is_lunch, is_fika, float(lead_time_min), float(lag_occ)]

    def generate_training_data(self, days: int = 28) -> Tuple[np.ndarray, np.ndarray]:
        """
        Synthesize multi-week realistic university schedule dataset
        incorporating lecture arrivals, dips during fika/lunch, and stochastic noise.
        """
        X = []
        y = []
        base_time = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)

        for day in range(days):
            current_day = base_time + timedelta(days=day)
            is_weekend = current_day.weekday() >= 5

            for minute_step in range(0, 24 * 60, 15):
                dt = current_day + timedelta(minutes=minute_step)
                hour = dt.hour + (dt.minute / 60.0)

                # Base expected occupancy for Room A109 (capacity: ~25 students)
                if is_weekend:
                    base_occ = 0 if np.random.rand() > 0.1 else np.random.randint(1, 4)
                else:
                    if 8.25 <= hour <= 10.0:
                        base_occ = np.random.randint(12, 22)
                    elif 10.0 < hour < 10.25:
                        base_occ = np.random.randint(2, 6) # Fika
                    elif 10.25 <= hour <= 12.0:
                        base_occ = np.random.randint(15, 25) # Lecture
                    elif 12.0 < hour < 13.0:
                        base_occ = np.random.randint(0, 3) # Lunch
                    elif 13.15 <= hour <= 15.0:
                        base_occ = np.random.randint(10, 20) # Lab/Seminar
                    elif 15.0 < hour < 15.25:
                        base_occ = np.random.randint(1, 5) # Afternoon Fika
                    elif 15.25 <= hour <= 17.0:
                        base_occ = np.random.randint(8, 16)
                    elif 17.0 < hour <= 20.0:
                        base_occ = np.random.randint(0, 6) # Study groups
                    else:
                        base_occ = 0

                # Generate multi-step forecast samples (lead times: 5, 10, 15, 20, 25, 30 min)
                for lead in [5, 10, 15, 20, 25, 30]:
                    future_dt = dt + timedelta(minutes=lead)
                    f_hour = future_dt.hour + (future_dt.minute / 60.0)

                    # Compute ground-truth future target
                    if is_weekend:
                        f_occ = base_occ
                    else:
                        if 8.25 <= f_hour <= 12.0 or 13.15 <= f_hour <= 17.0:
                            f_occ = int(base_occ * 0.9 + np.random.randint(-2, 3))
                            if 12.0 <= f_hour < 13.0: f_occ = np.random.randint(0, 3)
                        else:
                            f_occ = max(0, int(base_occ * 0.5))

                    f_occ = max(0, min(30, f_occ))
                    features = self._extract_features(dt, lead, base_occ)
                    X.append(features)
                    y.append(f_occ)

        return np.array(X), np.array(y)

    def train(self) -> Dict[str, Any]:
        """Train the Random Forest Regressor on timetable patterns."""
        if RandomForestRegressor is None:
            logger.warning("scikit-learn not available, using analytical heuristic fallback.")
            return self.metrics

        logger.info("Training Model 01 (Random Forest Occupancy Forecaster)...")
        X, y = self.generate_training_data(days=28)

        # Train 80/20 train/test split
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        rf = RandomForestRegressor(n_estimators=50, max_depth=12, min_samples_split=4, random_state=42, n_jobs=-1)
        rf.fit(X_train, y_train)

        preds = rf.predict(X_test)
        r2 = float(r2_score(y_test, preds))
        rmse = float(math.sqrt(mean_squared_error(y_test, preds)))

        self.model = rf
        self.metrics = {
            "algorithm": "RandomForestRegressor (n_estimators=50, max_depth=12)",
            "r2_score": round(r2, 4),
            "rmse": round(rmse, 4),
            "samples": len(X),
            "trained_at": datetime.now(timezone.utc).isoformat()
        }

        try:
            joblib.dump(self.model, self.model_file)
            logger.info(f"Model 01 trained successfully! R2: {r2:.4f}, RMSE: {rmse:.4f}. Saved to {self.model_file}")
        except Exception as e:
            logger.warning(f"Could not persist model file: {e}")

        return self.metrics

    def load_or_train(self):
        """Load trained model from disk or train a new one."""
        if joblib and os.path.exists(self.model_file):
            try:
                self.model = joblib.load(self.model_file)
                self.metrics["trained_at"] = "Loaded from disk"
                logger.info(f"Loaded existing Model 01 from {self.model_file}")
                return
            except Exception as e:
                logger.warning(f"Failed to load {self.model_file}: {e}. Retraining...")
        self.train()

    def predict_horizon(self, 
                        current_time: datetime, 
                        current_occupancy: int, 
                        horizon_steps: int = 6, 
                        step_dt_sec: float = 300.0) -> List[int]:
        """
        Predict future occupancy for each step in the lookahead horizon.
        Returns: list of predicted integers [occ_1, occ_2, ..., occ_H].
        """
        step_min = step_dt_sec / 60.0
        predicted_schedule = []

        if self.model is None:
            # Fallback heuristic if ML lib is absent
            return [current_occupancy] * horizon_steps

        try:
            for step in range(1, horizon_steps + 1):
                lead_min = step * step_min
                features = np.array([self._extract_features(current_time, lead_min, current_occupancy)])
                pred = self.model.predict(features)[0]
                
                # Autoregressive persistence blend:
                # The immediate next step (k=1) has strong correlation with current sensor observation;
                # subsequent steps decay toward the schedule expectation.
                decay = math.exp(-0.4 * (step - 1))
                blended = (decay * current_occupancy) + ((1.0 - decay) * pred)
                predicted_schedule.append(max(0, int(round(blended))))

            return predicted_schedule
        except Exception as e:
            logger.error(f"Prediction error in OccupancyForecaster: {e}")
            return [current_occupancy] * horizon_steps

