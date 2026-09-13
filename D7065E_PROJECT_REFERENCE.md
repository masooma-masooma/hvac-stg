# D7065E — Embedded Intelligence at the Edge
## Comprehensive Systems Engineering & Architectural Reference Guide
**Student Project:** Smart HVAC & Climate Optimization System  
**Author:** Masooma Masooma  
**Course:** D7065E / 17073 — Luleå University of Technology (LTU)  
**Academic Credits:** 7.5 ECTS | **Evaluation Standard:** Grade 5 Target  
**Core Frameworks & Tools:** Go (Golang), Docker, Docker Compose, BuildSim, MQTT (Mosquitto), TimescaleDB / Parquet, D2 (C4 Model), LaTeX

---

## 1. Executive Course Overview & Pedagogical Objectives

### 1.1 Course Philosophy & Core Philosophy
D7065E is fundamentally a **Systems Engineering and Distributed Systems Architecture** course, not an abstract AI modeling course. The goal is to build an autonomous, cyber-physical system (CPS) that senses, reasons, decides, and acts in a closed loop within a physical or simulated environment (BuildSim).

Key tenets emphasized by course staff:
1. **Specification-First Development (MBSE):** System models (C4 architecture, requirements matrices, sequence diagrams, state machines) must be defined before writing code. Tests must be derived directly from specifications.
2. **Closed-Loop Cyber-Physical Control:** Actuators must alter the physical simulation environment, which subsequently changes future sensor observations. Open loops or mocked actuator responses are strictly penalized.
3. **Decoupled Microservice Isolation:** Every sensor gateway, actuator controller, and logic service must run as an independent process in a standalone container with its own lifecycle (`docker start`, `docker stop`, `docker restart`).
4. **Architectural Decoupling & Telemetry Pipeline:** Direct REST calls from sensors to decision agents are **strictly prohibited**. Telemetry must flow through an asynchronous message broker (MQTT) and structured storage (hot/cold data pipeline).
5. **Separation of Concerns (Actuator vs. Decision Agent):** Decision services propose commands; dedicated actuator microservices validate authorization, safety bounds, and rate limits before applying changes to BuildSim.
6. **Resilience & Fault Tolerance:** The system must survive component crashes, network latency, sensor drift, and stuck actuators. A restarted process must automatically rediscover BuildSim, re-register, and resume operation without breaking the loop.
7. **Whiteboard Oral Defense:** The final individual examination requires drawing the entire end-to-end architecture from memory on a whiteboard and defending every single protocol, data contract, failure recovery path, and trade-off.

---

## 2. System Environment: BuildSim & OccupancySim Deep-Dive

### 2.1 What is BuildSim?
BuildSim is the authoritative digital twin server of the LTU A-House. It acts as the shared building state manager and 3D visualizer.
- **Binary / Container:** Single Go binary listening on port `9090` (default: `http://127.0.0.1:9090` or `http://buildsim:9090` inside Docker networks).
- **In-Memory State:** BuildSim retains state only in memory. When restarted, all registered equipment, sensors, and actuators are cleared. Student systems **must** feature automated re-registration / seeding logic.
- **No Native Physics:** BuildSim does **NOT** simulate thermodynamics, CO2 dissipation, or airflow. The student project must implement a **Physical Simulator** process that advances room physics and updates BuildSim sensors.
- **REST & WebSockets:** REST API for mutations; WebSocket streams for real-time 3D browser client rendering.

### 2.2 Key BuildSim API Endpoints & Data Contracts
| Endpoint | Method | Purpose & Payload Contract |
| :--- | :--- | :--- |
| `/api/building` | `GET` | Returns floors (`level0`, `level1`, `level2`) and room definitions |
| `/api/equipment/bulk` | `POST` | Atomically registers equipment, sensors, and actuators |
| `/api/sensors/{id}/value` | `PUT` | Updates sensor reading (`{"data_type": "text", "value": "21.5"}`) |
| `/api/sensors/{id}` | `GET` | Reads current sensor state |
| `/api/actuators/{id}/state`| `PUT`| Sets actuator target (`{"state": "22.0"}`) |
| `/api/actuators/{id}` | `GET` | Reads current actuator state |
| `/api/occupancy` | `GET/PUT` | Building occupancy map keyed by `<level>/<room>` (e.g. `level0/A109`) |
| `/api/entities` | `GET/PUT` | Dynamic coordinates of occupants/robots on walkable graph |
| `/api/room-layers` | `GET/PUT` | Spatial heatmaps/risk layers (e.g. simulated temperature, IAQ risk) |
| `/api/alerts` | `GET/PUT` | Real-time decision alerts displayed in 3D UI |
| `/api/sessions` | `GET` | Active browser viewer sessions |
| `/api/sessions/{id}/highlights` | `PUT` | Highlights specific rooms with color and opacity |

> **Critical Wire Rule:** In BuildSim, all sensor `value` and actuator `state` fields are **strings** (e.g. `"21.5"`, `"1"`). Your code must parse and format them explicitly.

### 2.3 What is OccupancySim?
The repository provides `occupancysim` (Go microservice + optional Svelte UI on `:8081`):
- Simulates realistic human movement across the A-house using Dijkstra pathfinding on walkable navigation graphs.
- Generates weekday schedules: arrivals (08:00–10:00), fika breaks (09:45, 14:30), lunch dips (12:00–13:00), lectures, and departures (16:00–19:00).
- Automatically writes to BuildSim:
  - `PUT /api/entities` (1 s tick)
  - `PUT /api/occupancy` (5 s tick)
- Our system can query `/api/occupancy` directly or ingest occupancy telemetry through IoT occupancy sensors.

---

## 3. Masooma's Project: Smart HVAC & Climate Optimization

### 3.1 Use Case Summary & Trade-off Tension
Heating and cooling dominate commercial building power draw. Fixed schedules waste enormous energy conditioning empty rooms or lag behind sudden thermal loads during large meetings.
- **The Core Conflict:** Thermal Comfort & Air Quality (ASHRAE 55 / EN 16798 standards: 20–24 °C, CO2 < 1000 ppm) vs. **Energy Minimization** (minimizing power draw and mechanical wear).
- **Physical Complexity:** Multi-zone thermal dynamics provide significant inertia, heat exchange between adjacent rooms, ambient solar/weather heat gains, and occupancy body heat (~100 W/person).

### 3.2 Target Architecture: Sense → Broker → Store → Predict → Decide → Act

```mermaid
flowchart TD
    subgraph BuildSim_Env["BuildSim Digital Twin (Port 9090)"]
        BS_Sensors["Sensor Registry (/api/sensors)"]
        BS_Actuators["Actuator Registry (/api/actuators)"]
        BS_Occ["Occupancy Registry (/api/occupancy)"]
        BS_Layers["Heatmap / Room Layers (/api/room-layers)"]
        BS_Viewer["3D Web Viewer (WebSocket)"]
    end

    subgraph Physics_Engine["Physical Simulation Service (Go)"]
        Physics["Thermodynamic & IAQ Engine<br/>• Thermal mass & envelope loss<br/>• Human metabolic heat & CO2<br/>• HVAC heating/cooling transfer"]
    end

    subgraph Edge_Telemetry["Edge Sensor Layer (Go Microservices)"]
        TempSens["Temperature Sensors"]
        CO2Sens["CO2 Sensors"]
        OccSens["Occupancy Sensors"]
    end

    subgraph Ingestion_Broker["Telemetry Pipeline"]
        Broker["MQTT Message Broker (Mosquitto)"]
        Pipeline["Pipeline Consumer & Quality Monitor (Go/Python)"]
        HotStore[("TimescaleDB / DuckDB<br/>Hot Window")]
        ColdStore[("Parquet Lake<br/>Cold Historical Archive")]
    end

    subgraph Decision_Core["Autonomous Controller (Go / Python)"]
        OccModel["Short-term Occupancy Forecaster"]
        ThermalModel["Dynamic Thermal Predictor"]
        MPC["Model Predictive Control (MPC)<br/>• Receding horizon optimization<br/>• Comfort bound constraints<br/>• Energy penalty minimization"]
        Baseline["Parallel Fixed-Schedule Baseline"]
        Simplex["Simplex Safety Guardrails<br/>(Clamp to safe regulatory limits)"]
    end

    subgraph Actuator_Layer["Actuator Microservices (Go)"]
        HVAC_Act["HVAC Actuator Process<br/>• Authority check<br/>• Rate limiting & dwell time<br/>• Command retry logic"]
    end

    subgraph Monitoring["Telemetry & Visual Analytics"]
        Dashboard["Real-time Optimization Dashboard<br/>• Continuous kWh savings vs baseline<br/>• Predicted vs actual trajectories<br/>• Sensor health & anomaly flags"]
    end

    %% Closed Loop Data Flow
    Physics -->|PUT /api/sensors/../value| BS_Sensors
    BS_Actuators -->|GET /api/actuators/..| Physics
    BS_Occ -->|GET /api/occupancy| Physics

    BS_Sensors -->|Poll / Stream| Edge_Telemetry
    Edge_Telemetry -->|Publish /telemetry/...| Broker
    Broker -->|Subscribe| Pipeline
    Pipeline -->|Store hot readings| HotStore
    Pipeline -->|Batch export| ColdStore
    Pipeline -->|Stuck/drift flags| MPC

    HotStore -->|Window query| OccModel
    HotStore -->|Window query| ThermalModel
    OccModel -->|Occupancy forecast| MPC
    ThermalModel -->|Thermal response matrix| MPC
    MPC -->|Proposed setpoint/damper| Simplex
    Simplex -->|Validated command [REST]| HVAC_Act
    HVAC_Act -->|PUT /api/actuators/../state| BS_Actuators

    HotStore -->|Metrics stream| Dashboard
    HVAC_Act -->|Energy log| Baseline
    Baseline -->|Comparative savings| Dashboard
    MPC -->|PUT /api/alerts & room-layers| BS_Layers
    BS_Layers --> BS_Viewer
```

---

## 4. The Engineering Specifications Required for Grade 5

To achieve a top grade, the project must adhere to the formal C4 Architectural Model, implement specification-driven development, and execute strict fault-injection testing.

### 4.1 Required C4 Diagrams (using D2)
1. **System Context (Level 1):** Defines human actors (Building Facilities Manager, Room Occupants), the core autonomous system boundary, BuildSim, and external cloud services.
2. **Container Architecture (Level 2):** High-level deployment units showing network boundaries:
   - Physical Simulator (Go)
   - Sensor Gateways (Go)
   - MQTT Broker (Mosquitto)
   - Pipeline Consumer & Quality Monitor (Go / Python)
   - TimescaleDB (Hot Store) & MinIO/Parquet (Cold Store)
   - Autonomous Controller & MPC Engine (Go / Python)
   - Actuator Microservice (Go)
   - Metrics Dashboard (Web/Go)
3. **Component Diagram (Level 3):** Internal breakdown of the Autonomous Controller container:
   - Feature Preprocessor & Window Aggregator
   - Occupancy Predictor
   - Thermal Simulator / Response Model
   - MPC Cost Optimizer (Objective: $J = \sum (\alpha \cdot \text{Energy} + \beta \cdot \text{ComfortViolation}^2)$)
   - Simplex Safety Filter & Dwell-Time Limiter
   - Actuator Client & Decision Audit Logger
4. **Dynamic Sequence Diagram:** Minute-by-minute message flow during a high-occupancy event (meeting start -> CO2 spike -> MPC re-plans -> Actuator speeds up fan -> Temperature holds steady -> Meeting ends -> Setback mode).
5. **Deployment Diagram:** Docker network topology, port mappings, restart policies, volume mounts, resource constraints (`cpus`, `memory`).
6. **State Transition Diagram:** Microservice lifecycle states: `Init` -> `Registering` -> `Normal Control` -> `Degraded (Safe Fallback)` -> `Self-Healing` -> `Terminated`.

### 4.2 Mathematical Physics & Physical Simulation Model
The Physical Simulator must run a discrete-time ODE state update every $\Delta t$ (e.g. 1 second):

#### Temperature Dynamics (Heat Balance):
$$C_{\text{room}} \frac{dT_{\text{in}}}{dt} = \frac{T_{\text{out}} - T_{\text{in}}}{R_{\text{wall}}} + \sum_{j \in \text{adj}} \frac{T_{j} - T_{\text{in}}}{R_{\text{int}}} + q_{\text{occ}} \cdot N_{\text{occ}} + q_{\text{solar}} + Q_{\text{HVAC}}$$

Where:
- $C_{\text{room}}$: Thermal capacitance of room air and furniture ($J/K$)
- $R_{\text{wall}}$: Thermal resistance of exterior building envelope ($K/W$)
- $R_{\text{int}}$: Thermal resistance between adjacent zones
- $q_{\text{occ}}$: Human metabolic sensible heat gain (~80–100 W/person)
- $Q_{\text{HVAC}}$: Thermal power injected or extracted by HVAC unit ($W$), governed by actuator setpoint and heating/cooling capacity.

#### Air Quality Dynamics (CO2 Mass Balance):
$$V_{\text{room}} \frac{dC}{dt} = G_{\text{occ}} \cdot N_{\text{occ}} - \dot{V}_{\text{vent}}(C - C_{\text{ambient}})$$

Where:
- $V_{\text{room}}$: Room volume ($m^3$)
- $G_{\text{occ}}$: CO2 generation rate (~0.005 L/s per person)
- $\dot{V}_{\text{vent}}$: Fresh air ventilation volumetric rate ($m^3/s$), controlled by ventilation damper actuator.
- $C_{\text{ambient}}$: Outdoor CO2 concentration (~420 ppm).

### 4.3 Control Strategies: MPC vs. Rule-Based Baseline
1. **Baseline Controller (Fixed Schedule / Thermostat):**
   - Fixed temperature setpoint ($21^\circ C$) from 07:00 to 18:00, unconditioned at night.
   - Fixed minimum ventilation rate.
2. **Autonomous MPC Controller:**
   - Evaluates a future horizon $H$ (e.g. 30–60 minutes ahead).
   - Uses forecasted occupancy to pre-cool or pre-heat spaces during low-cost periods or allow gentle drift before departures.
   - Cost Function:
     $$\min_{\{u_t\}} \sum_{t=1}^{H} \left( w_e \cdot P_{\text{HVAC}}(u_t) + w_c \cdot \max(0, T_t - T_{\text{max}})^2 + w_c \cdot \max(0, T_{\text{min}} - T_t)^2 + w_{\text{iaq}} \cdot \max(0, C_t - 1000)^2 \right)$$
   - **Actuator Chattering Prevention:** Dwell time constraint (no setpoint change within 5 minutes of previous adjustment).

---

## 5. Fault Injection & Verification Matrix

The course explicitly assesses system resilience under simulated failures.

| Test ID | Fault Injected | Expected Autonomous Behavior | Pass Criteria |
| :--- | :--- | :--- | :--- |
| `FIT-01` | **BuildSim Process Crash** (`docker restart buildsim`) | Microservices catch connection refusal, backoff with exponential retry, re-register equipment on recovery. | Zero crash loop, state fully restored in < 10 s. |
| `FIT-02` | **MQTT Broker Disconnect** (`docker stop mosquitto`) | Sensor services buffer readings in local ring buffers. Pipeline alerts controller. | Buffered data flushed on reconnect; zero message drop. |
| `FIT-03` | **Frozen / Stuck Sensor** (Simulated constant reading) | Pipeline data-quality monitor detects zero rolling variance; flags sensor as degraded. | Controller switches to nominal fallback setpoint without runaway heating. |
| `FIT-04` | **Actuator Failure / Network Drop** | Actuator fails to acknowledge command. | Controller logs failure; re-attempts or alerts operator; avoids state corruption. |
| `FIT-05` | **Unrealistic Setpoint Injection** (Simulated adversarial command) | Simplex safety filter in actuator interceptor rejects command outside $16^\circ\text{C} \le T \le 28^\circ\text{C}$. | Out-of-bounds command rejected; error logged; safe boundary maintained. |

---

## 6. Recommended Technology Stack & Project Structure

### 6.1 Language & Tool Distribution
- **Go (Golang 1.23+):**
  - High-performance, concurrent, lightweight microservices (`sensor-service`, `actuator-service`, `physical-simulator`).
  - Small Docker scratch/alpine images (< 25 MB).
- **Python 3.12+:**
  - Data science, ML feature engineering, and MPC solver (`scipy.optimize`, `numpy`, `pandas`, `pyarrow`).
- **Infrastructure:**
  - **MQTT Broker:** `eclipse-mosquitto:2`
  - **Time-Series Storage:** TimescaleDB (`postgres:16` with timescale plugin) or embedded DuckDB.
  - **Cold Storage:** Local Parquet files partition by date/room.
  - **Orchestration:** `docker compose` with explicit health checks and restart policies.
  - **Diagrams:** D2 (`oss.terrastruct.com/d2`) compiled to SVG/PNG.
  - **Documentation:** LaTeX (`pdflatex` / `pandoc`) adhering to course report template.

### 6.2 Recommended Workspace Layout for `c:\university\staging`

```text
staging/
├── notes/                           # Original course PDFs & syllabus
├── D7065E_PROJECT_REFERENCE.md      # This comprehensive systems engineering guide
├── docker-compose.yml               # Complete system orchestrator
├── Makefile                         # Build, test, diagram rendering, simulation runner
├── buildsim/                        # BuildSim binary & initialization scripts
│   └── seed_equipment.go            # Idempotent equipment registration
├── cmd/                             # Go microservices entrypoints
│   ├── simulator/                   # Physical thermal & CO2 simulation engine
│   ├── sensor-gateway/              # BuildSim poller -> MQTT publisher
│   └── actuator-controller/         # Command validator -> BuildSim actuator updater
├── internal/                        # Reusable Go domain packages
│   ├── buildsimclient/              # Robust REST & WebSocket client
│   ├── physics/                     # Heat balance & mass balance differential models
│   └── telemetry/                   # MQTT payload serialization & schemas
├── analytics/                       # Python ML & Control Services
│   ├── pipeline/                    # MQTT consumer -> TimescaleDB / Parquet store
│   ├── models/                      # Occupancy forecaster & thermal predictor
│   └── controller/                  # MPC optimization engine & baseline runner
├── dashboard/                       # Real-time Web / Terminal visualizer
├── diagrams/                        # C4 architecture models in D2
│   ├── context.d2
│   ├── container.d2
│   ├── component.d2
│   ├── dynamic.d2
│   └── deployment.d2
└── tests/                           # Verification & Fault-Injection Suites
    ├── integration_test.go
    └── fault_injection.py
```

---

## 7. Next Strategic Steps & Execution Roadmap

1. **Week 2 Milestone (Proposal & Repo Finalization):**
   - Confirm proposal matches the course template (already well-aligned).
   - Verify BuildSim runs locally on port 9090 and loads the LTU A-house 3D view.
2. **Week 3 Checkpoint (Architecture & Design Specification):**
   - Render the formal C4 diagrams in D2.
   - Define exact JSON schemas for MQTT telemetry topics (`building/level0/+/sensor/+`).
   - Define REST API contracts between Controller and Actuators.
3. **Core MVP (End-to-End Loop Closure):**
   - Deploy single room (e.g. `level0/A109`):
     `Physical Simulator` $\to$ `BuildSim Sensor` $\to$ `Sensor Microservice` $\to$ `MQTT` $\to$ `Rule Controller` $\to$ `Actuator Microservice` $\to$ `BuildSim Actuator` $\to$ `Physical Simulator`.
   - Verify closed-loop response (actuator changes alter temperature).
4. **Scale & Advanced Intelligence:**
   - Multi-room expansion.
   - Parquet data logging and baseline energy comparator.
   - Deploy MPC optimizer and live savings dashboard.
5. **Fault Injection & Whiteboard Defense Preparation:**
   - Execute failure test matrix.
   - Review architectural decisions for the oral defense.

