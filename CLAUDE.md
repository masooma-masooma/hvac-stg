# Autonomous Cyber-Physical Smart HVAC System (D7065E)

## Project Overview
Autonomous Cyber-Physical Smart HVAC System for LTU A-House Room A109.
- **Course:** D7065E — *Embedded Intelligence at the Edge*, Luleå University of Technology (LTU), 7.5 ECTS
- **Candidate:** Masooma Masooma (solo project)
- **Examiner:** Johan Kristiansson
- **Target Grade:** Grade 5 (autonomous sense-reason-act loop, MPC optimization, edge ML models, fault-injection resilience, honest self-critique)

---

## Architecture & Microservices
The system runs via Docker Compose on a bridged network `hvac-network`:

| Container Name | Service / Role | Tech Stack | Port / URL | Key Responsibility |
| :--- | :--- | :--- | :--- | :--- |
| `buildsim` | Digital twin | University binary | `:9090` | In-memory state of LTU A-House (sensors, actuators, occupancy, 3D view) |
| `mosquitto` | Telemetry Broker | Eclipse Mosquitto | `:1883` | Decoupled MQTT message broker (QoS 0) |
| `physical-simulator` | Room physics | Go | Internal | 1 s Euler integration of temperature/CO2 for A109, writes sensors; re-seeds A109 after BuildSim restarts |
| `sensor-gateway` | Edge Sensor Gateway | Go | Internal | Polls BuildSim sensors at 2 Hz, publishes `building/level0/A109/telemetry` with the sensor's own timestamp |
| `telemetry-ingestor` | Ingestor & Power Model | Go | `:8081` | Validates ranges, drops unchanged (duplicate/stale) readings, reads live setpoint/damper from BuildSim, computes power, writes SQLite WAL |
| `smart-controller` | Autonomous MPC & ML | Python 3.11 (FastAPI) | `:8082` | Receding-horizon MPC with Model 01 + Model 02, Safe Fallback on stale data, baseline twin, WebSocket `/ws/telemetry` |
| `actuator-controller` | Safety guardrail | Go | `:8080` | `POST /commands`: clamps setpoint 16–28 °C and damper 0–3, rejects unknown rooms, writes BuildSim actuators |
| `hvac-dashboard` | Web Operator UI | Go static server / JS | `:3000` | Live charts over WebSocket with automatic REST fallback |

Room physics is defined once in `services/smart-controller/models/dynamics.py`; `cmd/simulator` and the ingestor power model (`cmd/ingestor`) implement the same equations and constants. Change all three together.

---

## Core Commands

### Docker Infrastructure
```powershell
docker compose up -d --build      # build and start all services
docker compose ps                 # status
docker compose logs -f smart-controller
docker compose down
```

### Test Suite
```powershell
# 1. Go unit tests (guardrail, ingestor power model + validation + actuator reads, SQLite migration + energy)
go test ./cmd/... ./internal/...

# 2. Python unit tests (tests/ is mounted into the container at /tests)
docker exec smart-controller pytest /tests/test_smart_controller.py -v -p no:cacheprovider

# 3. Closed-loop end-to-end test against the running stack (host Python, needs `requests`)
python tests/test_e2e_integration.py
```

### Benchmarks
```powershell
# Fault injection / recovery (restarts containers; writes data/resilience_benchmark.json)
python scripts/test_resilience.py

# Simulated work week: fixed schedule vs rule-based vs MPC (writes data/benchmarks/scenario_benchmark.json, ~10 min)
docker exec smart-controller python /scripts/benchmark_scenarios.py

# Retrain/evaluate both models (writes data/benchmarks/ml_models_benchmark.json)
docker exec smart-controller python /scripts/train_models.py
# Retrain the live Model 02 on logged telemetry (only use a window logged under the current physics)
curl -X POST "http://localhost:8082/api/models/retrain?minutes=60"
```

---

## Machine Learning Models & Optimization
1. **Model 01 — Occupancy Forecaster (`models/occupancy_forecaster.py`):** Random Forest regressor predicting occupancy 5–30 min ahead from local-time calendar features (LTU lecture blocks, lunch, fika) and current occupancy. Trained on a synthetic 8-week LTU timetable with random lecture cancellations; labels are the occupancy actually observed at t + lead.
2. **Model 02 — Thermal/IAQ Predictor (`models/thermal_model.py`):** Two gradient-boosted regressors (ΔT, ΔCO2) on physics-informed features. Trained on transitions extracted from logged telemetry (which now stores the live setpoint/damper) plus a synthetic corpus from the room physics. Power is derived from the predicted trajectory by an energy balance, not learned.
3. **Receding-Horizon MPC (`controller/mpc.py`):** 6 steps × 5 min (30 min). Move blocking: the action may change at three block starts (default steps 1, 2 and 4; the third block is aligned with the first forecast occupied/empty transition) → 20³ = 8000 candidate sequences scored in vectorised batches. Stage cost: energy (kWh) + occupied-only tracking of 21.5 °C, a 20–24 °C band penalty, a soft CO2 penalty above 800 ppm and a hard one above 1000 ppm; frost protection (16 °C) when empty; small switching cost. Step 1 uses max(measured, forecast) occupancy.
4. **Dwell-time filter:** 2 control cycles (20 s) between action changes, bypassed when CO2 > 1100 ppm or the room is occupied and below 18 °C.
5. **Safe Fallback (`controller/safety.py`):** if the newest telemetry row is older than 30 s (or the ingestor is unreachable) the MPC is skipped and 21.0 °C / damper 1 is commanded.

---

## Verified Status
Go tests pass, 18 Python tests pass, E2E passes, fault injection 5/5. Simulated work week: MPC uses 53.8% less energy than the fixed schedule and has by far the least occupied time above 1000 ppm CO2 (0.2%, vs 12.7% for rule-based + DCV and 27.9% for fixed/rule-based), but uses 18.4% more energy than a rule-based thermostat and 9.1% more than rule-based + DCV. Report these numbers honestly; do not reintroduce the old 85.2 / 34.7 / 55.5% claims.

See `report/FINAL_ACADEMIC_REPORT.md` for the full write-up. Measured results live in:
- `data/benchmarks/scenario_benchmark.json` — simulated work-week energy/comfort comparison
- `data/resilience_benchmark.json` — fault-injection recovery times
- `data/benchmarks/ml_models_benchmark.json` — model metrics

Scope deviations from the proposal (documented in the report's limitations): single room (A109) instead of multi-zone, no humidity sensing, SQLite WAL + JSONL export instead of Parquet, no thermal heatmap on our dashboard (BuildSim's 3D view shows the temperature layer), time-compressed physics.

---

## Key Files Reference
- `report/FINAL_ACADEMIC_REPORT.md` — academic write-up
- `report/WHITEBOARD_DEFENSE_PLAYBOOK.md` — oral exam preparation
- `report/main.tex` — IEEE LaTeX version of the report
- `PROJECT_CONTEXT_HANDOFF.md` — architectural handoff
- `tests/test_smart_controller.py`, `tests/test_e2e_integration.py` — Python unit + E2E tests
- `scripts/test_resilience.py`, `scripts/benchmark_scenarios.py`, `scripts/train_models.py`
- `docker-compose.yml`
