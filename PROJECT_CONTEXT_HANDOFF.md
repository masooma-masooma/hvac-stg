# Project Context & Architecture Handoff

**Project:** Autonomous Cyber-Physical Smart HVAC System, LTU A-House Room A109
**Course:** D7065E — *Embedded Intelligence at the Edge*, LTU (7.5 ECTS)
**Candidate:** Masooma Masooma (solo) · **Examiner:** Johan Kristiansson · **Target:** Grade 5
**Workspace:** `C:\university\staging`

This document describes the system as it is implemented and measured. Every figure comes from a result file in `data/` and was produced by the scripts named next to it.

---

## 1. Sense–Reason–Act Loop

1. **Sense.** `physical-simulator` integrates the room physics every second and writes `A109-temp`, `A109-co2` and `A109-occ` into BuildSim. `sensor-gateway` polls them at 2 Hz and publishes a bundle to `building/level0/A109/telemetry` (MQTT, QoS 0), carrying each sensor's own timestamp.
2. **Store.** `telemetry-ingestor` validates the bundle (all three readings present; temperature −20…60 °C; CO2 300…5000 ppm; occupancy 0…200) and stores it only if the sensor timestamp advanced. For each stored row it reads `A109-setpoint` and `A109-damper` from BuildSim, stores them with the row and computes power from them. SQLite in WAL mode at `data/db/hvac.db`.
3. **Reason.** Every 10 s `smart-controller` fetches the last 2 minutes from `GET /api/history`. If the newest row is older than 30 s, it enters **Safe Fallback** (21.0 °C, damper 1). Otherwise Model 01 forecasts occupancy for six 5-minute steps, Model 02 rolls 8000 action sequences forward, and the MPC chooses the cheapest. A 20 s dwell filter limits action changes.
4. **Act.** `POST /commands` to `actuator-controller`, which clamps setpoint to 16–28 °C and damper to 0–3, rejects rooms other than A109, and `PUT`s the state to BuildSim. The simulator reads it on its next tick.
5. **Observe.** The controller pushes a snapshot every second on `ws://<host>:8082/ws/telemetry`; the dashboard (:3000) falls back to REST polling when the socket is down.

## 2. Services

| Container | Stack | Port | Notes |
| :--- | :--- | :--- | :--- |
| `buildsim` | University binary | 9090 | In-memory; loses equipment on restart |
| `mosquitto` | Eclipse Mosquitto 2 | 1883 | Anonymous, local only |
| `physical-simulator` | Go | — | `cmd/simulator`; re-seeds A109 every 15 s if missing; publishes a temperature layer for BuildSim's 3D view |
| `sensor-gateway` | Go | — | `cmd/sensor-gateway` |
| `telemetry-ingestor` | Go | 8081 | `cmd/ingestor`, `internal/storage`; `/healthz` and `/api/stats` report accepted/rejected/duplicate counts |
| `smart-controller` | Python 3.11, FastAPI | 8082 | `services/smart-controller`; mounts `tests/`, `scripts/`, `data/benchmarks/` |
| `actuator-controller` | Go | 8080 | `cmd/actuator-controller` |
| `hvac-dashboard` | Go static server + JS | 3000 | `services/dashboard` |

**Room physics is defined in three places that must stay identical:** `services/smart-controller/models/dynamics.py`, `cmd/simulator/main.go` (`heaterRate`, `stepRoom`) and `cmd/ingestor/main.go` (`CalculatePowerW`). Constants: $k_{hvac}=0.04$, $k_{env}=0.005$, $k_{vent}=0.008$ per damper level, $k_{occ}=0.01$ °C/s per person, CO2 1.5 ppm/s per person, ventilation $0.01+0.02D$ s⁻¹, power $25 + 45D + 8750h$ W (heating ≤ 2.5 kW), outdoor 12 °C / 420 ppm.

## 3. Models and Controller

- **Model 01** (`models/occupancy_forecaster.py`): Random Forest, local-time LTU calendar features + current occupancy, 5–30 min ahead. Synthetic 8-week timetable with cancellations. Test RMSE 1.26 persons (persistence 3.60), MAE 1.05 on the unseen benchmark week.
- **Model 02** (`models/thermal_model.py`): two gradient-boosted regressors for ΔT and ΔCO2 on physics-informed features. Trained on logged transitions (rows now include setpoint/damper) plus a synthetic corpus. R² 0.9967 (ΔT), 0.9993 (ΔCO2). Power from an energy balance. Retrain the live model with `POST /api/models/retrain?minutes=N`; only use a window logged under the current physics.
- **MPC** (`controller/mpc.py`): 6 × 5 min; setpoints {18, 19.5, 20.5, 21.5, 22.5} × dampers {0–3}; three move blocks (default 1/2/3 steps, third block aligned with the first forecast occupancy transition) → 8000 sequences; mean solve 215 ms. Cost: energy + occupied-only comfort tracking (21.5 °C), 20–24 °C band, CO2 soft (>800) and hard (>1000) penalties, empty-room frost floor, small switching cost.
- **Safety** (`controller/safety.py`): 30 s staleness threshold → 21 °C / damper 1.
- **Baseline twin** (`comparator/baseline.py`): fixed schedule (22 °C / damper 2 from 06:00–22:00 local time, else 16 °C / damper 0) simulated with the same physics and the measured occupancy. Comfort is only counted while occupied.

## 4. Verification

| What | Command | Latest result |
| :--- | :--- | :--- |
| Go unit tests | `go test ./cmd/... ./internal/...` | all pass |
| Python unit tests | `docker exec smart-controller pytest /tests/test_smart_controller.py -v -p no:cacheprovider` | 18 passed |
| Closed-loop E2E | `python tests/test_e2e_integration.py` | pass |
| Fault injection | `python scripts/test_resilience.py` | 5/5 pass |
| Work-week benchmark | `docker exec smart-controller python /scripts/benchmark_scenarios.py` | see below |
| Model metrics | `docker exec smart-controller python /scripts/train_models.py` | see Section 3 |

**Work week** (`data/benchmarks/scenario_benchmark.json`):

| | Fixed | Rule-based | Rule + DCV | MPC |
| :--- | ---: | ---: | ---: | ---: |
| Energy (kWh) | 112.7 | 44.0 | 47.7 | 52.1 |
| Occupied time in 20–24 °C | 100.0% | 99.7% | 99.7% | 99.8% |
| Occupied time CO2 > 1000 ppm | 27.9% | 27.9% | 12.7% | 0.2% |

MPC: 53.8% less energy than fixed; 18.4% more than rule-based; 9.1% more than DCV; by far the least occupied time above 1000 ppm (0.2%).

**Fault injection** (`data/resilience_benchmark.json`): broker restart 1.1 s · BuildSim wipe 3.8 s · Safe Fallback entered 37.4 s after simulator stop, MPC back 10.0 s after restart · controller restart 7.6 s · guardrail clamps 48 → 28 °C and 4 → 16 °C.

## 5. Issues Found and Fixed (Audit of 2026-10-06)

Earlier documentation claimed fixes that were not in the code. The audit found and fixed:
- Ingestor power used a hard-coded 21 °C / damper 1; it now reads the live actuator state, and its test (which referenced a non-existent function) compiles and passes.
- No ingestor range validation and no controller staleness check existed; both are implemented and tested.
- The dashboard polled with hard-coded `localhost`; `/ws/telemetry` did not exist. Both are fixed.
- The MPC kept the damper at maximum in an empty room (switching cost larger than any one-step saving), only optimised the first step, and its reason strings did not match its actions.
- Occupant heat was ~50× too strong and the radiator never reached its setpoint; heating power was charged forever. Ventilation was undersized for a full lecture.
- Model 01's labels could never show a lecture starting; Model 02 omitted current CO2 from its CO2 features. Schedules used UTC instead of Swedish local time.
- The repository's pytest file failed 2 of 8 tests, while a different, edited copy inside the container passed; there is now one copy, mounted into the container.
- The resilience JSON did not match its script's output, and its broker test counted rows from before the restart. Both are fixed; benchmarking is now a separate script.
- The reported savings (85.2 / 34.7 / 55.5 %, "32.1–65.0 %") could not be reproduced and were replaced by the work-week benchmark.

## 6. Known Limitations
Single room; time-compressed physics (room settles in ~1 min, so pre-heating has little value); models trained mostly on synthetic data from the evaluation equations; fixed outdoor temperature; no ventilation heat recovery; QoS 0 telemetry; static guardrail; frequent MPC action changes (231 per simulated week). Not implemented from the proposal: multi-zone control, humidity, Parquet, heatmaps on our own dashboard.

## 7. Files
```
cmd/actuator-controller/   guardrail + tests
cmd/ingestor/              ingestor + tests
cmd/sensor-gateway/        2 Hz gateway
cmd/simulator/             room physics + re-seeding
internal/storage/          SQLite (WAL, migration, energy integration) + tests
services/smart-controller/ main.py, controller/{mpc,safety}.py, models/{dynamics,occupancy_forecaster,thermal_model}.py, comparator/baseline.py
services/dashboard/        static UI (WebSocket + REST fallback)
tests/                     test_smart_controller.py, test_e2e_integration.py
scripts/                   test_resilience.py, benchmark_scenarios.py, train_models.py
report/                    FINAL_ACADEMIC_REPORT.md, main.tex, WHITEBOARD_DEFENSE_PLAYBOOK.md, figures/
diagrams/                  Mermaid sources (*.mmd) and renders
data/                      db/ (SQLite), benchmarks/, resilience_benchmark.json
```
