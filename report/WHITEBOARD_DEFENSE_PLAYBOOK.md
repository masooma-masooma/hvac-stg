# D7065E — Whiteboard Oral Defense Playbook
## Smart HVAC Optimisation, Room A109
**Candidate:** Masooma Masooma
**Examiner:** Johan Kristiansson
**Target:** Grade 5

All numbers below come from `data/benchmarks/scenario_benchmark.json`, `data/benchmarks/ml_models_benchmark.json` and `data/resilience_benchmark.json`. If you re-run the benchmarks, re-check them.

---

## 1. Whiteboard Sketch (5 minutes)

```
 DIGITAL TWIN            INGESTION                 DECISION                     ACTUATION
┌──────────────┐  REST  ┌──────────────┐        ┌──────────────────────┐     ┌──────────────────┐
│ BuildSim     │──────▶│ Sensor        │        │ Smart controller     │REST │ Actuator         │
│ :9090        │ 2 Hz   │ gateway (Go)  │        │ :8082                │────▶│ guardrail :8080  │
└──▲───────▲───┘        └──────┬───────┘        │  staleness > 30 s?   │     │ clamp 16–28 °C,  │
   │       │                   │ MQTT QoS 0     │   ├ yes → Safe       │     │ damper 0–3       │
   │       │            ┌──────▼───────┐        │   │   Fallback 21/1  │     └────────┬─────────┘
   │       │            │ Mosquitto    │        │   └ no → Model 01    │              │ PUT
   │       │            │ :1883        │        │      + Model 02      │              │ actuators
   │       │            └──────┬───────┘        │      + MPC (8000     │              ▼
   │       │                   │                │        sequences)    │          BuildSim
   │  ┌────┴─────────┐  ┌──────▼───────┐  REST  │      + dwell 20 s    │
   │  │ Physical     │  │ Ingestor     │──────▶│                       │──WebSocket──▶ Dashboard :3000
   └──│ simulator    │  │ validate,    │ history└──────────────────────┘
      │ 1 s physics, │  │ dedup, read  │
      │ re-seeding   │  │ actuators,   │
      └──────────────┘  │ power, SQLite│
                        └──────────────┘
```

Corner notes to write:
- $\frac{dT}{dt} = h - (0.005 + 0.008D)(T - 12) + 0.01N$; the radiator thermostat $h$ settles at the setpoint and cannot cool.
- $\frac{dC}{dt} = 1.5N - (0.01 + 0.02D)(C - 420)$
- $P = 25 + 45D + 8750h$ W
- MPC: 6 × 5 min, 20 actions, 3 move blocks → 8000 sequences, solved in ≈215 ms.

Key numbers:
- Work week: MPC **53.8% less energy than fixed schedule**, **only 0.2% of occupied time above 1000 ppm**, but **18.4% more energy than a rule-based thermostat** and **9.1% more than DCV**.
- Model 01: RMSE 1.26 persons (persistence 3.60). Model 02: R² 0.9967 (temperature change).
- Recovery: broker 1.1 s · BuildSim wipe 3.8 s · Safe Fallback after 37.4 s · controller restart 7.6 s.

---

## 2. Two-Minute Pitch

> "Fixed HVAC schedules heat empty rooms and ventilate full rooms too little. I built an autonomous controller for room A109 in the A-House digital twin that senses, predicts, decides and acts.
>
> Sensors are polled at 2 Hz and published over MQTT. The ingestor rejects implausible values and stores a row only when the sensor's own timestamp advances, so frozen sensors become visible as stale data. It also reads the actual setpoint and damper back from BuildSim, so the power it logs reflects what the room is really doing.
>
> Every 10 seconds the controller checks that its data is less than 30 seconds old. If it isn't, it goes to a safe fixed setting. Otherwise Model 01, a Random Forest, forecasts occupancy for the next 30 minutes, and Model 02, a gradient-boosted predictor, rolls 8000 candidate action sequences forward. The MPC picks the cheapest one in energy, comfort and CO2, applies only the first step and re-plans. Commands go through a guardrail that clamps them to safe ranges.
>
> Over a simulated work week the MPC used about half the energy of a fixed schedule and spent the least time above 1000 ppm CO2 by a wide margin: 0.2% of occupied time. But I also compared it to simple occupancy-based rules, and they used less energy than the MPC, while exceeding the CO2 limit. So most of the saving comes from knowing the room is empty; the MPC's added value here is air quality. I'll explain why in the limitations."

---

## 3. Examiner Questions

### Q1. Why WebSockets for the dashboard and not MQTT or REST? *(Johan's proposal feedback)*
> "Each boundary has a different interaction pattern. Sensor telemetry is fan-out, so MQTT: the gateway doesn't care who is listening, and consumers can restart without affecting it. Commands need an immediate, explicit answer: did the guardrail accept it, clamp it, or did BuildSim fail? That's REST with status codes, and setting a state is idempotent, so retries are safe. The dashboard needs the server to push state to a browser. WebSocket is built into browsers; MQTT would need a WebSocket listener on Mosquitto plus a JS client library. SSE would also work for one-way push. I chose WebSocket so operator commands could later share the channel. The dashboard reconnects with back-off, falls back to REST polling, and shows a banner if data is more than 5 s old."

### Q2. What happens if the MQTT broker goes down?
> "Gateway and ingestor reconnect automatically; the first new row arrived 1.1 s after the broker came back. Telemetry is QoS 0, so readings published during the outage are lost. I don't claim zero loss. If the outage lasts more than 30 s, the controller sees stale data and goes to Safe Fallback."

### Q3. BuildSim keeps everything in memory. What if it restarts?
> "The physical simulator checks every 15 s whether the A109 equipment exists and re-registers it if not. In the last run telemetry was flowing again after 3.8 s; it can take up to about 15 s depending on where the watchdog is in its cycle."

### Q4. How do you detect a broken sensor or stale data?
> "Two layers. The ingestor rejects bundles with missing readings or implausible values: temperature outside −20 to 60 °C, CO2 outside 300 to 5000 ppm, negative occupancy. It also only stores a row if the sensor timestamp advanced. The gateway forwards BuildSim's timestamp rather than the poll time, so if the simulator freezes, rows stop. The controller checks the age of the newest row; above 30 s it skips the MPC and commands 21 °C, damper 1. When I stopped the simulator, Safe Fallback started 37.4 s later (30 s threshold plus up to one 10 s cycle), and the MPC resumed 10.0 s after the simulator came back."

### Q5. What if the smart controller itself crashes?
> "The actuators keep their last setpoint, so the room stays in a sane state, and Docker's restart policy restarts the process. In the test the MPC was dispatching again 7.6 s later. The ingestor keeps recording throughout because it doesn't depend on the controller."

### Q6. Explain your MPC. What exactly is optimised?
> "Six 5-minute steps. Twenty candidate actions: five setpoints times four damper levels. The action can change at three points, and the third change point is moved to wherever Model 01 says the room becomes occupied or empty, giving 8000 sequences. Model 02 predicts temperature and CO2 for all of them in a batch, and power comes from an energy balance on those predictions. The cost is energy in kWh, plus, only when occupied, tracking 21.5 °C, a heavy penalty outside 20–24 °C, a soft CO2 penalty above 800 and a heavy one above 1000. When empty there's only a 16 °C frost floor. There's a small switching cost. I apply the first action and re-plan every 10 s."

### Q7. How do you prevent chattering?
> "A small switching cost in the objective, and a dwell filter that blocks action changes within 20 s of the previous one, except in emergencies (CO2 above 1100 ppm, or an occupied room below 18 °C). I had to learn this the hard way: an earlier version had a large switching cost, and since a damper change only affected the first 5-minute step, it never paid off. The damper stayed at maximum in an empty room. There's a regression test for that now. Honestly, the MPC still changed action 231 times in the simulated week versus 46 for the rule-based controller, so a real deployment would need a longer dwell."

### Q8. Are your energy savings real?
> "In the simulated week, against a fixed schedule: 53.8% less energy overall. Nearly all of it is during empty periods; while occupied the MPC actually used 14.7% more, because it ventilates more. I also compared against two stronger baselines: a rule-based thermostat and the same with CO2-driven ventilation. Those used less energy than the MPC (the MPC used 18.4% and 9.1% more), but they exceeded 1000 ppm 27.9% and 12.7% of occupied time, against 0.2% for the MPC. So the honest conclusion: most of the saving comes from occupancy awareness, and the MPC trades some energy for air quality."

### Q9. Then why use MPC at all?
> "Two reasons. It handles several coupled objectives (energy, temperature, CO2, switching) in one explicit cost that I can tune and explain, and it acts on predictions instead of reacting after CO2 has already risen, which is why it almost never exceeds 1000 ppm. In my simulated room, temperature settles in about a minute, so pre-heating is worth very little. In a real building with hour-scale thermal inertia, the forecast would matter much more. That's the main limitation of my evaluation."

### Q10. Why MPC over reinforcement learning or PID?
> "PID tracks one setpoint and reacts to error; it can't trade energy against CO2 or use a forecast. RL could learn a policy, but it needs a large number of episodes, it's hard to debug, and it gives no explicit reason for a decision. MPC lets me print exactly what it predicted and why it chose an action. That's in every decision log line. RL was the proposal's stretch goal and I didn't attempt it."

### Q11. How good are your models, and what are they trained on?
> "Model 01 is a Random Forest. Its error is 1.26 persons against 3.60 for 'assume nothing changes', and 1.05 persons on the benchmark week it never saw. It's trained on a synthetic LTU timetable with random cancellations, labelled with the occupancy actually observed later, so it learns lecture arrivals. Model 02 is gradient-boosted trees: R² 0.9967 for temperature change and 0.9993 for CO2. It's trained on 4 516 transitions from my own logged telemetry plus 20 000 synthetic ones. Most data is synthetic and comes from the same equations as the simulator, so these numbers are optimistic; real LTU data would be the next step."

### Q12. Why SQLite instead of TimescaleDB or Parquet?
> "This is an edge deployment with one room. SQLite in WAL mode is embedded in the ingestor, lets the controller read while the ingestor writes, and the recent-window query is indexed. The proposal said Parquet; I replaced it with SQLite plus a JSONL export, because I needed low-latency recent-window queries for control more than columnar analytics. For many rooms and long histories I'd add a columnar archive."

### Q13. Is your safety guardrail a Simplex architecture?
> "No, and I corrected that wording. It's a deterministic range clamp: 16–28 °C, damper 0–3, unknown rooms rejected. A real Simplex architecture would switch to a verified safety controller based on reachability or invariants. My Safe Fallback is closer in spirit, but it's triggered by data staleness, not by a verified safety envelope."

### Q14. What didn't you deliver from the proposal?
> "Multi-zone control, humidity sensing, Parquet, and heatmaps on my own dashboard. BuildSim's 3D view shows the temperature layer I publish. Everything in the core loop was delivered: MQTT sensors, both models, MPC, REST actuation, fixed-schedule and rule-based baselines, crash recovery and the live WebSocket dashboard."

### Q15. What are the main limitations?
> "Time-compressed physics, so pre-conditioning is undervalued. One room. Synthetic training data from the same equations as the evaluation. Fixed outdoor temperature. No ventilation heat recovery, so ventilation is expensive in my model. QoS 0 telemetry. A static guardrail. And too much action switching for real equipment."

---

## 4. Live Demo Script
1. Open `http://localhost:3000`. Show "Live: WebSocket push" and "MPC active" badges.
2. Click **Small Group (5p)** or **Crowded Lecture (15p)**. Within one cycle the decision log shows "MPC: occupied (…) … Model 02 predicts …", and the MPC chart shows the forecast vs actual occupancy.
3. Click **Empty Room (0p)**. The log shows "Model 01 forecasts no arrivals … Eco setback".
4. Click **Test Safety Guardrail (45 °C)**. Response: clamped to 28.0 °C; the MPC restores its own setpoint within 10 s.
5. Optional: `docker compose stop simulator`. After ~40 s the badge turns red "SAFE FALLBACK". `docker compose start simulator` returns to MPC.
