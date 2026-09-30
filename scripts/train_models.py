"""
Offline Model Training & Retraining Script
Trains Model 01 (Random Forest Occupancy Forecaster) and Model 02 (Thermal Predictor)
on historical database telemetry and academic timetable features.
Directly implements the Weeks 4-5 and Weeks 6-7 Roadmap Deliverables.
"""

import sys
import os
import json

# Add smart-controller to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "smart-controller"))

from models.occupancy_forecaster import OccupancyForecaster
from models.thermal_model import ThermalPredictor

def main():
    print("=================================================================")
    print("   D7065E - Offline Machine Learning Model Training Pipeline     ")
    print("=================================================================")
    
    print("\n[1/2] Training Model 01: Random Forest Occupancy Forecaster...")
    occ_model = OccupancyForecaster()
    m1_metrics = occ_model.train()
    print(f" -> Model 01 Complete! Algorithm: {m1_metrics['algorithm']}")
    print(f" -> Performance: R2 = {m1_metrics['r2_score']:.4f}, RMSE = {m1_metrics['rmse']:.4f} occupants")
    print(f" -> Training Samples: {m1_metrics['samples']}")

    print("\n[2/2] Training Model 02: Polynomial Ridge Thermal Predictor...")
    therm_model = ThermalPredictor(ambient_temp=12.0, ambient_co2=420.0)
    m2_metrics = therm_model.train_from_telemetry_and_synthetic()
    print(f" -> Model 02 Complete! Algorithm: {m2_metrics['algorithm']}")
    print(f" -> Performance: R2 = {m2_metrics['r2_score']:.4f}, RMSE = {m2_metrics['rmse']:.4f} °C")
    print(f" -> Training Samples: {m2_metrics['samples']}")

    results = {
        "model_01_occupancy_forecaster": m1_metrics,
        "model_02_thermal_predictor": m2_metrics
    }

    output_path = os.path.join("data", "ml_models_benchmark.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[OK] Model training benchmark saved to: {output_path}")

if __name__ == "__main__":
    main()
