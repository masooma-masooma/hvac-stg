# D7065E: Instructor Feedback & Protocol Justification

**Student:** Masooma Masooma  
**Course:** D7065E — Embedded Intelligence at the Edge (LTU)  
**Instructor:** Johan Kristiansson  
**Date Received:** Sep 15 at 1:12 PM  
**Proposal Status:** **Approved / Very Good**  

---

## 1. The Instructor's Feedback

> **"Proposal looks very good. Could you motivate in the final report why use web sockets rather than other alternatives, eg you also mentioned MQTT."**  
> — *Johan Kristiansson*

---

## 2. Context & Course Alignment

In **D7065E Course Notes 2 (Section: *Choose a communication route*)**, every communication protocol must be explicitly justified against its interaction pattern:

| Interaction Pattern | Recommended Protocol | Design Questions Required by Course |
| :--- | :--- | :--- |
| **Fan observations out to independent consumers** | **MQTT 5.0** (OASIS) | What are the topics, sessions, retained-message policy, delivery mode, and broker-outage behaviour? |
| **Push live state to a connected browser** | **WebSocket (RFC 6455)** | How does the client reconnect, detect stale state, and resynchronise? |
| **Read state / submit command with immediate result** | **HTTP / REST (RFC 9110)** | What is the timeout? Is retry safe? Is the operation idempotent? What does each failure status mean? |

Johan's question asks us to formalize the architectural division of labor between **MQTT** (which we use for telemetry) and **WebSockets** (which we use for the live dashboard).

---

## 3. Core Architectural Motivation: Why WebSockets for the Dashboard?

In our system architecture, communication is split into two distinct tiers:

```
┌─────────────────┐       MQTT (Pub/Sub)        ┌─────────────────┐
│ IoT Sensors     ├────────────────────────────►│ Data Pipeline / │
└─────────────────┘  (Edge-to-backend fan-out)  │ TimescaleDB     │
                                                └────────┬────────┘
                                                         │
                                               WebSocket │ (Push streaming)
                                                         ▼
                                                ┌─────────────────┐
                                                │ Live Dashboard  │
                                                │ (Browser / 3D)  │
                                                └─────────────────┘
```

### Why WebSockets over MQTT for the Web Dashboard?

1. **Native Browser Runtime Support (Zero Heavy Client Bundling):**
   * Web browsers natively implement the RFC 6455 WebSocket API (`new WebSocket("ws://...")`). 
   * MQTT is a TCP-level protocol. Browsers cannot open raw TCP sockets. To run MQTT in a browser, one must bundle an external JavaScript library (e.g., `paho-mqtt` or `mqtt.js`), run an MQTT-over-WebSocket bridge inside Mosquitto, and handle MQTT session handshakes inside the client application bundle.
   * WebSockets avoids this unnecessary protocol layering for UI rendering.

2. **Ultra-Low Framing Overhead for Real-Time Heatmaps:**
   * After the single initial HTTP handshake, WebSocket frames require only **2 to 10 bytes** of framing overhead per message.
   * This allows streaming 1–2 Hz thermal heatmap matrices, CO2 contours, and occupancy coordinates to the dashboard without saturating the browser's event loop or network stack.

3. **Alignment with BuildSim's Native Architecture:**
   * BuildSim itself exposes its real-time 3D viewport, highlights, and entity movements to the browser over WebSockets (`/api/sessions`). 
   * Adopting WebSockets for our performance dashboard maintains architectural consistency across the entire front-end tier.

4. **Bi-Directional Full-Duplex Channel (Future-Proofing for Operator Overrides):**
   * WebSockets is full-duplex. While the dashboard primarily consumes telemetry from the backend, the same socket allows the building manager to issue manual overrides (e.g., emergency purge, setpoint lock) back to the backend without establishing a secondary HTTP connection.

---

## 4. Why Not the Other Alternatives?

| Alternative | How It Works | Why It Was Rejected for the Dashboard |
| :--- | :--- | :--- |
| **REST Polling (`fetch` / `setInterval`)** | Client requests state every 1 second | Heavy overhead: repeated HTTP headers (500+ bytes per request), high connection churn, server-side request parsing, and inherent polling lag. |
| **Server-Sent Events (SSE)** | Unidirectional HTTP text stream from server | Unidirectional only (server $\to$ client). Cannot handle bi-directional interactive operator commands over the same stream. SSE is also restricted to UTF-8 text, whereas WebSockets supports efficient binary payloads. |
| **MQTT directly in Browser** | MQTT encapsulated inside WebSocket frames | Unnecessary complexity: requires exposing an MQTT WebSocket port on Mosquitto, bundling heavy client-side MQTT parsing libraries in the frontend, and managing MQTT keep-alives in JavaScript. |
| **gRPC-Web** | Protocol Buffers over HTTP/2 | Requires a special Envoy proxy translator to convert browser HTTP/1.1 requests to HTTP/2 gRPC, introducing unnecessary architectural friction for a laptop-scale lab setup. |

---

## 5. Complete Communication Protocol Matrix (For Final Report & Defense)

| Layer & Boundary | Protocol | Interaction Pattern | Why This Protocol Was Chosen |
| :--- | :---: | :--- | :--- |
| **Sensor Processes $\to$ Pipeline** | **MQTT** | Asynchronous Pub/Sub (`building/level0/+/sensor/+`) | Decouples producer from consumer; tiny 2-byte header; broker buffers messages during service restarts; allows multiple subscribers (pipeline, logger, monitor). |
| **Pipeline $\to$ Live Dashboard** | **WebSocket** | Persistent Bi-Directional Push Stream | Native browser API; 2-byte framing; sub-second streaming of thermal heatmaps; enables manual operator overrides. |
| **Controller $\to$ Actuators** | **REST (HTTP)** | Synchronous Request / Response (`POST /commands`) | Point-to-point command validation; immediate HTTP status codes (`200 OK`, `422 Unprocessable Entity`); clear failure boundaries. |
| **Actuators $\to$ BuildSim** | **REST (HTTP)** | Synchronous Idempotent Mutations (`PUT /api/actuators/{id}/state`) | Follows BuildSim's official state contract; idempotent updates; atomic error reporting. |

---

## 6. Whiteboard Oral Defense Talking Script

When asked by the examiner during your individual whiteboard defense:

> *"We deliberately chose different protocols for different architectural boundaries based on their interaction patterns:*
> 
> *1. **At the edge (Sensors to Backend), we use MQTT** because it provides topic-based pub/sub decoupling. If our controller or database restarts, sensors continue publishing without connection errors, and multiple consumers can read the same stream.*
> 
> *2. **Between the backend and the browser dashboard, we use WebSockets** because WebSockets is a native browser standard with only 2 bytes of framing overhead. Running MQTT in a browser requires an MQTT-over-WebSocket bridge and third-party JS libraries, which adds unnecessary complexity. WebSockets gives us low-latency, full-duplex streaming for live thermal heatmaps and allows operator overrides over the same connection.*
> 
> *3. **For control commands (Controller to Actuators), we use REST** because commands require synchronous, deterministic acknowledgement and explicit HTTP error codes rather than fire-and-forget messaging."*

