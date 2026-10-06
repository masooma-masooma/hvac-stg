"""
Model 01: Occupancy Forecaster
Random Forest regression predicting Room A109 occupancy 5-30 minutes ahead from
calendar features (local Swedish time, LTU lecture blocks, lunch, fika) and the
currently measured occupancy.

Training data is a synthetic multi-week LTU timetable: each day is simulated as
a 5-minute occupancy timeline (lectures with random cancellations and attendance,
evening study groups, sparse weekends). Samples pair the features at time t with
the occupancy actually observed at t + lead, so the model learns arrivals at the
start of lecture blocks as well as departures.
"""

import os
import math
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
from zoneinfo import ZoneInfo

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
MODEL_VERSION = 2
LOCAL_TZ = ZoneInfo("Europe/Stockholm")
SLOT_MIN = 5
SLOTS_PER_DAY = 24 * 60 // SLOT_MIN

# LTU lecture blocks (local time, hours) and typical attendance range
LECTURE_BLOCKS = [
    (8.25, 10.0, (12, 22)),
    (10.25, 12.0, (15, 25)),
    (13.25, 15.0, (10, 20)),
    (15.25, 17.0, (8, 16)),
]


def simulate_day_occupancy(day: datetime, rng: np.random.Generator) -> np.ndarray:
    """Occupancy for each 5-minute slot of a local calendar day."""
    occ = np.zeros(SLOTS_PER_DAY, dtype=int)
    hours = np.arange(SLOTS_PER_DAY) * SLOT_MIN / 60.0
    if day.weekday() >= 5:
        if rng.random() < 0.15:
            start = rng.uniform(10.0, 15.0)
            mask = (hours >= start) & (hours < start + rng.uniform(1.0, 2.5))
            occ[mask] = rng.integers(1, 4)
        return occ

    for start, end, (lo, hi) in LECTURE_BLOCKS:
        if rng.random() < 0.15:          # cancelled lecture
            continue
        attendance = int(rng.integers(lo, hi + 1))
        mask = (hours >= start) & (hours < end)
        noise = rng.integers(-2, 3, mask.sum())
        occ[mask] = np.clip(attendance + noise, 0, 30)
        first = np.argmax(mask)
        occ[first] = attendance // 2      # arrivals trickle in
    if rng.random() < 0.4:               # evening study group
        mask = (hours >= 17.25) & (hours < 17.25 + rng.uniform(0.75, 2.0))
        occ[mask] = rng.integers(2, 7)
    return occ


class OccupancyForecaster:
    def __init__(self, model_file: str = MODEL_PATH):
        self.model_file = model_file
        self.model = None
        self.metrics: Dict[str, Any] = {"trained_at": None}
        self.feature_names = [
            "hour_float", "day_of_week", "is_weekend", "is_lecture_block",
            "is_lunch", "is_fika", "lead_time_min", "lag_occupancy",
        ]
        self.load_or_train()

    def _extract_features(self, dt: datetime, lead_time_min: float, lag_occ: int) -> List[float]:
        """Calendar features of the *target* time (now + lead) in local time, plus current occupancy."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        future = (dt + timedelta(minutes=lead_time_min)).astimezone(LOCAL_TZ)
        hour = future.hour + future.minute / 60.0
        dow = future.weekday()
        is_weekend = 1.0 if dow >= 5 else 0.0
        is_lecture = 0.0
        if not is_weekend and any(s <= hour < e for s, e, _ in LECTURE_BLOCKS):
            is_lecture = 1.0
        is_lunch = 1.0 if (not is_weekend and 12.0 <= hour < 13.0) else 0.0
        is_fika = 1.0 if (not is_weekend and ((10.0 <= hour < 10.25) or (15.0 <= hour < 15.25))) else 0.0
        return [hour, float(dow), is_weekend, is_lecture, is_lunch, is_fika, float(lead_time_min), float(lag_occ)]

    def generate_training_data(self, days: int = 56, seed: int = 42) -> Tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(seed)
        start = datetime(2026, 8, 31, tzinfo=LOCAL_TZ)   # a Monday
        timeline = []
        for d in range(days + 1):
            timeline.append(simulate_day_occupancy(start + timedelta(days=d), rng))
        occ = np.concatenate(timeline)

        X, y = [], []
        leads = [5, 10, 15, 20, 25, 30]
        for i in range(days * SLOTS_PER_DAY):
            t = start + timedelta(minutes=i * SLOT_MIN)
            for lead in leads:
                X.append(self._extract_features(t, lead, int(occ[i])))
                y.append(int(occ[i + lead // SLOT_MIN]))
        return np.array(X), np.array(y)

    def train(self) -> Dict[str, Any]:
        if RandomForestRegressor is None:
            logger.warning("scikit-learn not available, using persistence forecast.")
            return self.metrics

        X, y = self.generate_training_data()
        split = int(len(X) * 0.8)            # chronological split: last ~11 days held out
        rf = RandomForestRegressor(n_estimators=60, max_depth=14, min_samples_leaf=3, random_state=42, n_jobs=1)
        rf.fit(X[:split], y[:split])
        preds = rf.predict(X[split:])
        persistence = X[split:, 7]
        self.model = rf
        self.metrics = {
            "algorithm": "RandomForestRegressor (n_estimators=60, max_depth=14)",
            "test_r2": round(float(r2_score(y[split:], preds)), 4),
            "test_rmse_persons": round(float(math.sqrt(mean_squared_error(y[split:], preds))), 3),
            "persistence_rmse_persons": round(float(math.sqrt(mean_squared_error(y[split:], persistence))), 3),
            "samples": int(len(X)),
            "trained_at": datetime.now(LOCAL_TZ).isoformat(),
        }
        logger.info(f"Model 01 trained: {self.metrics}")
        if joblib:
            try:
                joblib.dump({"version": MODEL_VERSION, "model": rf, "metrics": self.metrics}, self.model_file)
            except Exception as e:
                logger.warning(f"Could not persist model file: {e}")
        return self.metrics

    def load_or_train(self):
        if joblib and os.path.exists(self.model_file):
            try:
                bundle = joblib.load(self.model_file)
                if isinstance(bundle, dict) and bundle.get("version") == MODEL_VERSION:
                    self.model = bundle["model"]
                    self.metrics = bundle.get("metrics", self.metrics)
                    logger.info(f"Loaded existing Model 01 from {self.model_file}")
                    return
                logger.info("Model 01 file has an old format, retraining.")
            except Exception as e:
                logger.warning(f"Failed to load {self.model_file}: {e}. Retraining...")
        self.train()

    def predict_horizon(self,
                        current_time: datetime,
                        current_occupancy: int,
                        horizon_steps: int = 6,
                        step_dt_sec: float = 300.0) -> List[int]:
        """Predicted occupancy at the end of each horizon step: [occ_1, ..., occ_H]."""
        if self.model is None:
            return [current_occupancy] * horizon_steps
        step_min = step_dt_sec / 60.0
        try:
            X = np.array([self._extract_features(current_time, k * step_min, current_occupancy)
                          for k in range(1, horizon_steps + 1)])
            return [max(0, int(round(p))) for p in self.model.predict(X)]
        except Exception as e:
            logger.error(f"Prediction error in OccupancyForecaster: {e}")
            return [current_occupancy] * horizon_steps
