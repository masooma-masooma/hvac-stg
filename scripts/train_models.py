"""
Offline Model Training & Evaluation Script
Trains Model 01 (Random Forest occupancy forecaster) and Model 02 (gradient-boosted
thermal/IAQ predictor) from scratch and writes their held-out metrics.

Run inside the smart-controller container (it has the ML dependencies and can reach the ingestor):
    docker exec smart-controller python /scripts/train_models.py
Results: data/benchmarks/ml_models_benchmark.json

The running service keeps its own model instances; to retrain the live Model 02 on
the latest logged telemetry use:  curl -X POST http://localhost:8082/api/models/retrain
"""

import json
import os
import sys
import tempfile

for candidate in ("/app", os.path.join(os.path.dirname(__file__), "..", "services", "smart-controller")):
    if os.path.isdir(os.path.join(candidate, "controller")):
        sys.path.insert(0, os.path.abspath(candidate))
        break

from models.occupancy_forecaster import OccupancyForecaster
from models.thermal_model import ThermalPredictor, fetch_training_telemetry

INGESTOR_URL = os.getenv("INGESTOR_URL", "http://telemetry-ingestor:8081")
OUT_PATH = os.getenv("MODELS_OUT", "/benchmarks/ml_models_benchmark.json")
TELEMETRY_MINUTES = int(os.getenv("TELEMETRY_MINUTES", "360"))   # only use rows logged under the current physics


def main():
    tmp = tempfile.mkdtemp()

    print("[1/2] Training Model 01: Random Forest occupancy forecaster...")
    m1 = OccupancyForecaster(model_file=os.path.join(tmp, "m1.joblib")).metrics
    print(f"      {m1}")

    print("[2/2] Training Model 02: gradient-boosted thermal/IAQ predictor...")
    try:
        rows = fetch_training_telemetry(INGESTOR_URL, minutes=TELEMETRY_MINUTES)
        print(f"      fetched {len(rows)} telemetry rows from the ingestor")
    except Exception as e:
        rows = None
        print(f"      ingestor unavailable ({e}); synthetic corpus only")
    m2 = ThermalPredictor(model_file=os.path.join(tmp, "m2.joblib"), telemetry_rows=rows).metrics
    print(f"      {m2}")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"model_01_occupancy_forecaster": m1, "model_02_thermal_predictor": m2}, f, indent=2)
    print(f"Saved {OUT_PATH}")


if __name__ == "__main__":
    main()
