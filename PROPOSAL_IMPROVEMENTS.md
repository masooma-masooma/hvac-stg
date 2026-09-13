# D7065E Project Proposal: Key Improvements & Revisions

**Project:** Smart HVAC & Climate Optimization System  
**Author:** Masooma Masooma  
**Course:** D7065E — Embedded Intelligence at the Edge (LTU)  

This document outlines the **4 critical improvement points** identified during the deep architectural review of the original proposal (`smart HVAC proposal.pdf`). Each point details the existing text, the underlying systems-engineering rationale, and the drop-in replacement text.

---

## Point 1: Correcting the BuildSim Physics Misconception

### Location in Proposal
Section: **`Core Unknowns & Technical Risks`** (Bullet 1)

### Original Text
> *"Simulation & Model Transfer: Risk that BuildSim's synthetic thermal data is too simple, causing models trained on it to fail or show savings that wouldn't transfer to a real building."*

### The Issue & Examiner Critique
BuildSim does **not** simulate thermal dynamics or generate synthetic sensor physics; it is strictly an in-memory state coordinator and 3D visualizer. Stating that BuildSim provides thermal data indicates a misunderstanding of the platform. In D7065E, the student's own system must supply the software process that models thermodynamic equations and pushes state to BuildSim.

### Recommended Replacement Text
> **● Thermal Dynamics & Loop Fidelity:** Risk that the software-based thermal model is too simplified to capture multi-zone heat transfer accurately. This is addressed by implementing a discrete-time thermodynamic balance process (incorporating ambient heat exchange, human metabolic heat, and HVAC power) validated against public building benchmarks (ASHRAE) to ensure meaningful closed-loop control.

---

## Point 2: Incorporating Indoor Air Quality ($\text{CO}_2$) & Ventilation

### Location in Proposal
Sections: **`Scenario`** and **`System Architecture`** (Bullet 1 & 3)

### Original Text
> Mentions only *"temperature, and humidity sensors"* and keeping *"predicted temperatures within comfort bounds"*.

### The Issue & Examiner Critique
HVAC stands for *Heating, **Ventilation**, and Air Conditioning*. If the controller only optimizes temperature, the easiest way to minimize energy in an occupied room is to shut off outside ventilation entirely. This violates building health standards (e.g., Swedish workplace regulations AFS 2020:1 / EN 16798). Introducing $\text{CO}_2$ creates the multi-objective tension evaluated in Grade-5 submissions:
$$\text{Energy Minimization} \quad \longleftrightarrow \quad \text{Thermal Comfort } (20\text{--}24^\circ\text{C}) \quad \longleftrightarrow \quad \text{Indoor Air Quality } (\text{CO}_2 < 1000\text{ ppm})$$

### Recommended Replacement Text

#### For Section 2 (`Scenario`):
> During a simulated work week, per-zone occupancy, temperature, and $\text{CO}_2$ sensors feed real-time data into an occupancy forecaster and thermal/IAQ model. The controller evaluates candidate HVAC settings to select temperature setpoints and ventilation damper levels that minimize energy draw while maintaining temperatures (20–24°C) and $\text{CO}_2$ (< 1000 ppm) within regulatory comfort bounds (ASHRAE 55 / AFS 2020:1). A live dashboard tracks continuous energy savings against a fixed-schedule baseline alongside predicted versus actual occupancy and thermal heatmaps.

#### For Section 3 (`System Architecture` — Sensors):
> **● Sensors & Message Broker (MQTT):** Containerized IoT sensor processes (temperature, $\text{CO}_2$, occupancy) poll BuildSim and publish scheduled zone readings to an MQTT broker (Mosquitto). Decoupled sub-services consume this data independently without disturbing the sensors.

---

## Point 3: Dual-Tier Storage Pipeline (Hot Store vs. Cold Parquet)

### Location in Proposal
Section: **`System Architecture`** (Bullet 2 — Data Pipeline & ML Models)

### Original Text
> *"A data pipeline writes raw readings to Parquet and generates ML features."*

### The Issue & Examiner Critique
Apache Parquet is an immutable columnar format designed for cold, batch-analytical queries (OLAP). It is inefficient for real-time sliding-window queries (e.g., querying the average $\text{CO}_2$ over the last 15 minutes every 10 seconds). The standard distributed systems pattern requires a **hot tier** for real-time control features and a **cold tier** for historical analysis.

### Recommended Replacement Text
> **● Data Pipeline & Dual-Tier Storage:** An ingestion pipeline consumes telemetry from MQTT into a dual-tier storage architecture: a lightweight time-series store (TimescaleDB / SQLite) maintains recent sliding windows for real-time MPC feature extraction, while historical data is batch-exported to Parquet files for offline model training and baseline energy analytics.

---

## Point 4: Actuator Dwell-Time & Safety Guardrails (Anti-Chattering)

### Location in Proposal
Section: **`The Autonomous Controller`**

### Original Text
> Focuses exclusively on MPC simulating action sequences and scoring them against energy and comfort thresholds, with no mention of physical actuation limits.

### The Issue & Examiner Critique
In cyber-physical systems, mathematical optimizers can oscillate rapidly between discrete action states (e.g., cycling compressors ON and OFF every minute), a failure mode known as **actuator chattering** that causes physical equipment failure. Furthermore, unbounded optimizers may produce unrealistic setpoints under anomalous inputs. Course staff assess whether systems incorporate runtime safety constraints (Course Notes 4: *Safety boundaries and runtime assurance*).

### Recommended Replacement Text
> To ensure physical stability and equipment longevity, the controller enforces actuator **dwell-time constraints** (preventing setpoint adjustments more frequently than once every 5 minutes to eliminate chattering). Additionally, a **Simplex safety supervisor** acts as an intermediary, clamping all optimizer outputs to strict regulatory boundaries ($16^\circ\text{C} \le T \le 28^\circ\text{C}$) before dispatching commands to actuator services. A simple rule-based thermostat serves as the initial benchmark and safe fallback mode.

---

## Summary Matrix

| # | Topic | Original State | Improved State | Primary Architectural Benefit |
|---|---|---|---|---|
| **1** | **Physics Engine** | Assumed from BuildSim | Explicit software model | Eliminates major technical misconception; satisfies course simulation requirement |
| **2** | **Controlled Variables** | Temperature only | Temperature + $\text{CO}_2$ | Creates multi-objective trade-off required for Grade-5 evaluation |
| **3** | **Data Architecture** | Parquet only | Hot DB + Cold Parquet | Resolves real-time sliding window query latency |
| **4** | **Actuator Safety** | Unconstrained MPC | Dwell-time + Simplex clamp | Prevents mechanical chattering and guarantees regulatory compliance |

