# D7065E — Whiteboard Oral Defense Playbookk
## Smart HVAC & Climate Optimization System
**Candidate:** Masooma Masooma  
**Examiner:** Johan Kristiansson & Course Staff | Luleå University of Technology (LTU)  
**Target Evaluation:** Grade 5  

This playbook provides a comprehensive guide for the **whiteboard presentation** and **examiner Q&A session** during the D7065E oral examination.

---

## 1. The Whiteboard Drawing Blueprint (5-Minute Sketch)

When you walk up to the whiteboard, divide the board into **four vertical columns / layers**:

```
┌─────────────────┬──────────────────┬─────────────────┬─────────────────┐
│ 1. DIGITAL TWIN │  2. INGESTION &  │ 3. AUTONOMOUS   │  4. ACTUATION & │
│   & SIMULATION  │     TELEMETRY    │    DECISION     │      SAFETY     │
├─────────────────┼──────────────────┼─────────────────┼─────────────────┤
│                 │                  │                 │                 │
│  ┌───────────┐  │  ┌────────────┐  │  ┌───────────┐  │  ┌───────────┐  │
│  │ BuildSim  │──┼─>│ Sensor     │  │  │ Smart MPC │  │  │ Simplex   │  │
│  │ Twin 9090 │  │  │ Gateway    │  │  │ Optimizer │  │  │ Safety    │  │
│  └─────▲─────┘  │  └─────┬──────┘  │  │ (:8082)   │  │  │ Clamp     │  │
│        │        │        │ MQTT    │  └─────┬─────┘  │  └─────▲─────┘  │
│  ┌─────▼─────┐  │  ┌─────▼──────┐  │        │ REST   │        │ REST   │
│  │ Physical  │  │  │ Mosquitto  │  │  ┌─────▼─────┐  │  ┌─────┴─────┐  │
│  │ Simulator │  │  │ Broker     │──┼─>│ Dwell     │──┼─>│ Actuator  │  │
│  │ (ODEs)    │  │  │ (:1883)    │  │  │ Filter    │  │  │ Controller│  │
│  └───────────┘  │  └─────┬──────┘  │  │ (60s)     │  │  │ (:8080)   │  │
│                 │        │         │  └───────────┘  │  └─────┬─────┘  │
│                 │  ┌─────▼──────┐  │                 │        │ PUT    │
│                 │  │ Telemetry  │  │  ┌───────────┐  │        ▼        │
│                 │  │ Ingestor   │──┼─>│ Baseline  │  │   (BuildSim     │
│                 │  │ SQLite WAL │  │  │ (ASHRAE   │  │    Actuators)   │
│                 │  │ (:8081)    │  │  │  90.1)    │  │                 │
│                 │  └────────────┘  │  └───────────┘  │                 │
│                 │                  │                 │                 │
└─────────────────┴──────────────────┴─────────────────┴─────────────────┘
                      ▲
                      │ Queries comparison & history
            ┌─────────┴─────────┐
            │ Web Dashboard     │
            │ (:3000)           │
            └───────────────────┘
```

### Essential Board Annotations:
1. **Label the Protocols on the Arrows:**
   - Sensors $\to$ Broker: `MQTT (QoS 0/1, 2 Hz)`
   - Ingestor $\to$ SQLite: `WAL mode (Sub-ms query)`
   - Ingestor $\to$ MPC: `HTTP GET /api/history`
   - MPC $\to$ Actuator: `HTTP POST /commands`
   - Actuator $\to$ BuildSim: `HTTP PUT /api/actuators/{id}/state`
   - Dashboard $\to$ BuildSim: `WebSocket (3D Twin visualizer)`
2. **Label the Physical Formulas in the Corner:**
   - $\frac{dT}{dt} = 0.04(T_{\text{set}} - T) + 0.005(T_{\text{out}} - T) + 0.03 \cdot N_{\text{occ}}$
   - $\frac{d\text{CO}_2}{dt} = 1.5 \cdot N_{\text{occ}} - (0.01 + 0.02 \cdot \text{Damper})(\text{CO}_2 - 420)$
3. **Label the Key Result Numbers:**
   - **Energy Savings:** `+32.1% to +65.0%`
   - **Broker Crash Recovery (MTTR):** `1.05 seconds`
   - **BuildSim State Wipe Self-Heal (MTTR):** `15.07 seconds`
   - **Simplex Clamp (MTTR):** `0.03 seconds`

---

## 2. The 2-Minute Presentation Pitch

> *"Good morning. In this project, we solved the classic trade-off in building cyber-physical systems: how to drastically reduce electrical heating energy without sacrificing human thermal comfort (ASHRAE 55) or indoor air quality ($\text{CO}_2 < 1000\text{ ppm}$).
>
> Commercial buildings overwhelmingly rely on static timer schedules that blast heating in empty rooms and lag behind when people arrive. To solve this, we built an autonomous, 8-microservice edge architecture connected to the LTU A-House digital twin (BuildSim).
>
> Our architecture follows three core systems engineering principles:
> 1. **Decoupled Telemetry:** Sensors poll at 2 Hz and publish to an MQTT broker. Telemetry is persisted in a local pure-Go SQLite database with Write-Ahead Logging for sub-millisecond sliding window queries.
> 2. **Multi-Objective Model Predictive Control:** Every 10 seconds, our Python MPC optimizer looks 30 minutes into the future across 6 steps. When students enter Room A109, it detects their metabolic heat (+75W per person), recognizes that the room is warming naturally to 22.9°C, and backs off the electrical radiator to standby (70W), while ramping up the ventilation damper to flush exhaled $\text{CO}_2$.
> 3. **Runtime Safety & Resilience:** Actuators are protected by a 60-second anti-chattering dwell time and a Simplex safety supervisor that clamps dangerous inputs. Furthermore, our services feature self-healing logic that automatically detects and re-registers missing equipment in 15 seconds if BuildSim ever crashes.
>
> When benchmarked against an ASHRAE 90.1 fixed commercial baseline running in parallel under identical conditions, our system achieved **32.1% to 65.0% energy savings** with zero comfort violations."*

---

## 3. Defense Against Tough Examiner Questions

### Q1: *"Why did you use WebSockets for some components, but MQTT for telemetry and REST for actuation?"* (Johan Kristiansson's prompt)
**Your Answer:**
> *"We implemented a hybrid communication architecture based on the specific requirements of each edge interaction:
> 1. **MQTT for Sensor Ingestion:** Sensor telemetry is high-frequency (2 Hz) and one-to-many. MQTT has a tiny 2-byte header, minimal memory footprint on edge devices, and fully decouples publishers from subscribers. If our storage service restarts, the broker buffers messages without blocking the sensor gateway.
> 2. **REST for Actuation:** Actuator mutations require strict synchronous transactional guarantees. With HTTP REST (`POST /commands`), the optimizer immediately receives an HTTP 200 OK or 400 Bad Request confirming that the Simplex safety supervisor authorized the command.
> 3. **WebSockets for Visual Dashboards:** WebSockets excel at full-duplex binary/text streaming to browser viewports (such as the BuildSim 3D engine). Using HTTP polling in a web browser would incur massive connection teardown overhead and 1 KB HTTP header bloat per frame."*

---

### Q2: *"BuildSim has no persistence and wipes all equipment from RAM when restarted. How does your system survive a crash?"*
**Your Answer:**
> *"BuildSim is strictly an in-memory coordinator. If BuildSim crashes or is restarted mid-session, all equipment in Room A109 is erased.
> In our Physical Simulator microservice (`cmd/simulator/main.go`), we implemented an **autonomous self-healing watchdog**. Every 15 seconds, it queries `GET /api/equipment/hvac-A109`. If BuildSim returns a 404 Not Found, the simulator immediately catches the error and executes an atomic `POST /api/equipment/bulk` payload, re-registering the HVAC unit, 3 sensors, and 2 actuators.
> In our empirical chaos tests, when we killed BuildSim, the system completely self-healed in **15.07 seconds** with zero human intervention."*

---

### Q3: *"What is actuator chattering, and how does your system prevent mechanical wear?"*
**Your Answer:**
> *"Actuator chattering occurs when an unconstrained digital optimizer changes actuator targets too frequently (e.g., oscillating a heating valve or compressor ON and OFF every few seconds). In physical HVAC equipment, this causes severe mechanical fatigue and premature component failure.
> We address this at two levels:
> 1. **Cost Function Penalty:** In our MPC cost function, we apply an actuation delta penalty $w_{\Delta u} \cdot |\Delta u|$ which penalizes switching states unless the energy or comfort payoff is significant.
> 2. **Dwell-Time Filter:** We enforce a hard 60-second dwell time constraint. If the optimizer proposes a new setpoint before 60 seconds have elapsed, the command is suppressed and the previous state is maintained, unless an emergency comfort violation occurs ($T < 18^\circ\text{C}$ or $\text{CO}_2 > 1100\text{ ppm}$)."*

---

### Q4: *"Why choose Model Predictive Control (MPC) over Reinforcement Learning (RL) or a classic PID controller?"*
**Your Answer:**
> *"We chose MPC for three critical systems-engineering reasons:
> 1. **Anticipatory Action (Pre-conditioning):** PID controllers are purely reactive—they only react *after* an error occurs. Buildings have high thermal mass and inertia. MPC looks ahead 30 minutes into the future, allowing it to pre-ventilate or pre-heat spaces before occupancy peaks arrive.
> 2. **Multi-Objective Constraints:** Unlike PID, MPC natively handles coupled multi-variable constraints (simultaneously managing temperature, $\text{CO}_2$, and electrical power).
> 3. **Explainability & Safety vs. RL:** Deep Reinforcement Learning operates as an unverified black-box policy that can hallucinate dangerous actions and requires millions of simulation episodes to converge. MPC relies on transparent, verifiable thermodynamic equations, ensuring predictable behavior and immediate debuggability at the edge."*

---

### Q5: *"Why did you use SQLite WAL mode instead of TimescaleDB or InfluxDB?"*
**Your Answer:**
> *"In Course Notes 3, the course staff recommended evaluating storage tiers based on deployment constraints. TimescaleDB requires a dedicated PostgreSQL server running in a heavy container consuming 200–300 MB of RAM.
> Because our goal is **embedded intelligence at the edge**, we selected pure-Go SQLite with Write-Ahead Logging (WAL). It runs embedded inside the microservice process, consumes less than 15 MB of RAM, supports concurrent readers without blocking writes, and provides sub-millisecond execution times for sliding-window queries indexed on `(room, timestamp DESC)`. For long-term archiving, we expose an append-only JSONL stream that can be batch-ingested into cold storage."*

---

### Q6: *"How did you verify that your energy savings are genuine and not just an artifact of turning heating off?"*
**Your Answer:**
> *"We built a parallel virtual baseline running the standard ASHRAE 90.1 commercial thermostat schedule under the **exact same outdoor weather ($12.0^\circ\text{C}$) and identical occupancy conditions**.
> Both controllers were evaluated concurrently. In an empty room, the Smart Controller reduced power from 473W to 70W (85.2% savings). During active occupancy of 5 students, the Smart Controller lowered the setpoint to 20.5°C because human body heat (+375W) warmed the room to 22.9°C, saving 34.7% energy.
> Crucially, our metrics engine tracked **comfort violation degree-minutes** and **$\text{CO}_2$ ppm-minutes**, proving that comfort violations were 0.0 deg-min during occupancy. The savings came from eliminating waste and exploiting free metabolic heat, not from degrading comfort."*

