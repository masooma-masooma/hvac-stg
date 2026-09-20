# Phase 1 (Step 01): Environment Scaffolding, BuildSim & MQTT Broker Setup

**Project:** Smart HVAC & Climate Optimization System  
**Course:** D7065E — Embedded Intelligence at the Edge (LTU, 7.5 ECTS)  
**Authors:** Masooma Masooma & Team  
**Target Environment:** Windows 11 (PowerShell & Docker Desktop)  

This guide provides an exhaustive, step-by-step tutorial for replicating the initial system deployment. Every step outlines **what** is being done, the **architectural reasoning** behind it, the **exact commands to run**, the **code snippets**, and a **detailed code breakdown**.

---

## Overview of Phase 1 (Step 01)

By the end of this guide, your computer will have:
1. A structured project workspace with an initialized Go module (`hvac`).
2. The **BuildSim 3D digital twin server** running in an isolated Docker container on port `9090`.
3. The **Eclipse Mosquitto MQTT broker** running in a Docker container on port `1883`.
4. Room **A109** registered inside BuildSim with 3 active sensors (temperature, $\text{CO}_2$, occupancy) and 2 active actuators (heating setpoint, ventilation damper).
5. A working 3D viewer viewable in Chrome or Edge at `http://localhost:9090`.

---

## Step 1: Workspace Scaffolding & Go Module Initialization

### 1. Short Description & Reasoning
* **What:** Create the root project directory and initialize a Go module named `hvac`.
* **Why:** The course recommends Go for lightweight edge microservices. Initializing a Go module creates a `go.mod` file, which sets the root package namespace (`module hvac`). This allows our sensor gateways, actuator controllers, and simulator scripts to cleanly import internal packages and manage dependencies without classpath errors.

### 2. Actual Process & Commands
Open **PowerShell** and execute:

```powershell
# 1. Create the project root directory
mkdir c:\university\staging
cd c:\university\staging

# 2. Initialize the Go module named 'hvac'
go mod init hvac
```

### 3. Code Snippet (`go.mod`)
This generates a file named `go.mod` in your root folder:

```go
module hvac

go 1.23
```

### 4. Code Explanation
* `module hvac`: Declares the base import path for all Go packages in this project. When we write internal packages later (e.g., `internal/models`), they will be imported as `import "hvac/internal/models"`.
* `go 1.23`: Specifies the minimum Go language version required to compile this project.

---

## Step 2: Integrating the BuildSim Digital Twin Server

### 1. Short Description & Reasoning
* **What:** Copy the official `buildingsim` codebase from the course repository into our workspace.
* **Why:** BuildSim is the university-provided digital twin representing the LTU A-house. It contains the floorplan geometries for Level 0, Level 1, and Level 2, as well as the REST API engine for registering sensors, reading actuator states, and streaming 3D WebGL graphics over WebSockets. We need its source code and multi-stage Dockerfile so Docker can compile and run it locally.

### 2. Actual Process & Commands
If you do not already have the course repository cloned, clone it temporarily to extract `buildingsim`:

```powershell
# 1. Temporarily clone the course repository
git clone https://github.com/eislab-cps/D7065E.git temp_repo

# 2. Copy the buildingsim folder into your workspace
Copy-Item -Recurse -Force temp_repo\buildingsim .\buildingsim

# 3. Clean up the temporary clone
Remove-Item -Recurse -Force temp_repo
```

### 3. Code Snippet (`buildingsim/Dockerfile`)
Examine the provided Dockerfile in `buildingsim/Dockerfile`:

```dockerfile
FROM golang:1.25-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go generate ./... && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/buildsim ./cmd

FROM scratch
COPY --from=build /out/buildsim /buildsim
USER 65532:65532
EXPOSE 9090
ENTRYPOINT ["/buildsim"]
CMD ["start", "--host", "0.0.0.0", "--port", "9090"]
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["/buildsim", "health", "--url", "http://127.0.0.1:9090/healthz"]
```

### 4. Code Explanation
* `FROM golang:1.25-alpine AS build`: **Stage 1 (Builder)** — Uses a lightweight Alpine Go container to download dependencies and compile the Go code.
* `RUN go generate ./... && CGO_ENABLED=0 go build ...`: Statically compiles BuildSim into a standalone binary with zero C library dependencies (`CGO_ENABLED=0`) and strips debugging symbols (`-s -w`) to reduce file size.
* `FROM scratch`: **Stage 2 (Runtime)** — Uses Docker's empty base image (`scratch`). The final container contains *only* the compiled `/buildsim` executable. This results in a microscopic, secure container image (~15 MB) that starts in milliseconds.
* `HEALTHCHECK`: Enables Docker to verify that BuildSim's `/healthz` endpoint responds before marking the container as "healthy".

---

## Step 3: Configuring the Local MQTT Message Broker (Mosquitto)

### 1. Short Description & Reasoning
* **What:** Create a configuration file `config/mosquitto.conf` for Eclipse Mosquitto.
* **Why:** In D7065E, **direct REST calls from sensors to decision controllers are strictly prohibited for primary telemetry**. Sensors must publish their readings asynchronously to a message broker. In Mosquitto version 2.0 and above, external network connections and unauthenticated access are blocked by default for security. We must explicitly configure Mosquitto to bind to `0.0.0.0:1883` and allow local unauthenticated traffic within our private Docker network.

### 2. Actual Process & Commands
Create the `config` directory and write `config/mosquitto.conf`:

```powershell
mkdir config
```

Create `config/mosquitto.conf` and paste the configuration below.

### 3. Code Snippet (`config/mosquitto.conf`)

```text
listener 1883 0.0.0.0
allow_anonymous true
persistence true
persistence_location /mosquitto/data/
log_dest stdout
```

### 4. Code Explanation
* `listener 1883 0.0.0.0`: Instructs the broker to accept incoming TCP connections on standard MQTT port `1883` from any network interface (essential for Docker container communication).
* `allow_anonymous true`: Allows local microservices to connect, publish, and subscribe without username/password authentication (suitable for a local lab environment).
* `persistence true`: Saves queued messages and session states to disk across restarts.
* `log_dest stdout`: Pipes broker traffic logs directly to `docker compose logs mosquitto` for real-time debugging.

---

## Step 4: Multi-Container Orchestration (`docker-compose.yml`)

### 1. Short Description & Reasoning
* **What:** Create the root `docker-compose.yml` file to orchestrate BuildSim and Mosquitto.
* **Why:** Rather than manually executing `docker build` and `docker run` commands with complex port-forwarding flags, Docker Compose defines our entire microservice ecosystem as code. It automatically provisions an isolated internal virtual network (`hvac-network`) where containers can communicate using their service names (e.g., `http://buildsim:9090` and `mosquitto:1883`). Specifying `name: hvac` at the top guarantees that Docker Desktop names the project group **`hvac`** instead of defaulting to the folder name.

### 2. Actual Process & Commands
Create `docker-compose.yml` in the root folder (`c:\university\staging`):

### 3. Code Snippet (`docker-compose.yml`)

```yaml
name: hvac

services:
  buildsim:
    build:
      context: ./buildingsim
      dockerfile: Dockerfile
    container_name: buildsim
    ports:
      - "9090:9090"
    restart: unless-stopped
    networks:
      - hvac-network

  mosquitto:
    image: eclipse-mosquitto:2
    container_name: mosquitto
    ports:
      - "1883:1883"
    volumes:
      - ./config/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
    restart: unless-stopped
    networks:
      - hvac-network

networks:
  hvac-network:
    driver: bridge
```

### 4. Code Explanation
* `name: hvac`: Sets the explicit Docker Compose project name, ensuring clean branding in Docker Desktop and terminal outputs.
* `buildsim`:
  * `build: context: ./buildingsim`: Builds the image from our local `buildingsim` folder.
  * `ports: "9090:9090"`: Maps port 9090 from the container to `http://localhost:9090` on your Windows machine so you can view the 3D model in your browser.
* `mosquitto`:
  * `image: eclipse-mosquitto:2`: Uses the official lightweight Mosquitto image from Docker Hub.
  * `volumes: ./config/mosquitto.conf:...:ro`: Mounts our local configuration file inside the container as read-only (`:ro`).
* `networks: hvac-network`: Connects both containers to a private bridge network so future containers (sensors, controllers) can resolve `buildsim` and `mosquitto` via internal DNS.

---

## Step 5: Building & Launching the Infrastructure

### 1. Short Description & Reasoning
* **What:** Build the Docker images and start both containers in detached mode (`-d`).
* **Why:** We need both services running concurrently in the background. We then verify that BuildSim's REST engine is live and discovering floors before we register equipment.

### 2. Actual Process & Commands
Run in PowerShell from `c:\university\staging`:

```powershell
# 1. Build and start containers in the background
docker compose up -d --build

# 2. Check running container status
docker compose ps
```

### 3. Verification Commands
Test that BuildSim is responding to HTTP requests:

```powershell
curl.exe -s http://127.0.0.1:9090/api/building
```

### 4. Expected Output
`docker compose ps` will show:
```text
NAME        IMAGE                 COMMAND                  SERVICE     STATUS                    PORTS
buildsim    hvac-buildsim         "/buildsim start --h…"   buildsim    Up (healthy)              0.0.0.0:9090->9090/tcp
mosquitto   eclipse-mosquitto:2   "/docker-entrypoint.…"   mosquitto   Up                        0.0.0.0:1883->1883/tcp
```

`curl.exe` will return the building hierarchy:
```json
{"name":"A-Building (LTU)","levels":[{"id":"level0","label":"Floor 0"},{"id":"level1","label":"Floor 1"},{"id":"level2","label":"Floor 2"}]}
```

---

## Step 6: Automated Equipment Registration (`scripts/seed_a109.go`)

### 1. Short Description & Reasoning
* **What:** Create and run an automated Go seeding script that registers Room A109's equipment into BuildSim.
* **Why:** **BuildSim maintains state exclusively in memory.** If the BuildSim container restarts, crashes, or is redeployed, all registered sensors and actuators vanish. A production-grade distributed system must have an idempotent (repeatable) setup program that can restore the building equipment tree at any time without manual `curl` calls.

### 2. Actual Process & Commands
Create a directory named `scripts`:

```powershell
mkdir scripts
```

Create `scripts/seed_a109.go` and paste the code below. Then execute:

```powershell
go run .\scripts\seed_a109.go
```

### 3. Code Snippet (`scripts/seed_a109.go`)

```go
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

// Data contracts matching BuildSim's JSON schemas
type SensorDefinition struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Type     string `json:"type"`
	DataType string `json:"data_type"`
	Unit     string `json:"unit"`
	Value    string `json:"value"`
}

type ActuatorDefinition struct {
	ID    string `json:"id"`
	Name  string `json:"name"`
	Type  string `json:"type"`
	State string `json:"state"`
}

type EquipmentDefinition struct {
	ID        string               `json:"id"`
	Name      string               `json:"name"`
	Type      string               `json:"type"`
	Category  string               `json:"category"`
	Level     string               `json:"level"`
	Room      string               `json:"room"`
	Status    string               `json:"status"`
	Sensors   []SensorDefinition   `json:"sensors"`
	Actuators []ActuatorDefinition `json:"actuators"`
}

func main() {
	baseURL := strings.TrimRight(os.Getenv("BUILDSIM_URL"), "/")
	if baseURL == "" {
		baseURL = "http://127.0.0.1:9090"
	}

	// Define Room A109 HVAC Unit with multi-sensor suite and dual actuators
	equipment := []EquipmentDefinition{
		{
			ID:       "hvac-A109",
			Name:     "HVAC Unit A109",
			Type:     "ac_unit",
			Category: "hvac",
			Level:    "level0",
			Room:     "A109",
			Status:   "running",
			Sensors: []SensorDefinition{
				{
					ID:       "A109-temp",
					Name:     "Indoor Temperature",
					Type:     "temperature",
					DataType: "text",
					Unit:     "°C",
					Value:    "20.5",
				},
				{
					ID:       "A109-co2",
					Name:     "Indoor CO2 Concentration",
					Type:     "co2",
					DataType: "text",
					Unit:     "ppm",
					Value:    "450",
				},
				{
					ID:       "A109-occ",
					Name:     "Room Occupancy",
					Type:     "occupancy",
					DataType: "text",
					Unit:     "persons",
					Value:    "0",
				},
			},
			Actuators: []ActuatorDefinition{
				{
					ID:    "A109-setpoint",
					Name:  "Heating Setpoint",
					Type:  "setpoint",
					State: "21.0",
				},
				{
					ID:    "A109-damper",
					Name:  "Ventilation Damper Level",
					Type:  "fan_speed",
					State: "1",
				},
			},
		},
	}

	body, err := json.Marshal(equipment)
	if err != nil {
		log.Fatalf("Failed to serialize equipment: %v", err)
	}

	client := &http.Client{Timeout: 10 * time.Second}
	url := baseURL + "/api/equipment/bulk"

	fmt.Printf("Seeding equipment to BuildSim at %s...\n", url)
	resp, err := client.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		log.Fatalf("Could not reach BuildSim: %v (is BuildSim running?)", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		log.Fatalf("BuildSim rejected seeding (%s): %s", resp.Status, string(respBody))
	}

	fmt.Println(" Successfully registered equipment in Room A109:")
	fmt.Println("  - Equipment : hvac-A109 (Level 0, Room A109)")
	fmt.Println("  - Sensors   : A109-temp (20.5 °C), A109-co2 (450 ppm), A109-occ (0 persons)")
	fmt.Println("  - Actuators : A109-setpoint (21.0 °C), A109-damper (level 1)")
}
```

### 4. Code Explanation
* **Data Contracts:** Defines strongly-typed Go structs matching BuildSim's JSON API schema. Notice that `value` and `state` are defined as `string` types because BuildSim represents all sensor readings and actuator states as strings on the wire.
* **Bulk Endpoint (`/api/equipment/bulk`):** Rather than making 6 separate HTTP requests (one for equipment, three for sensors, two for actuators), the bulk API registers the entire hierarchical tree in a single atomic transaction.
* **Verification Command:**
  Query the registered equipment back from BuildSim:
  ```powershell
  curl.exe -s http://127.0.0.1:9090/api/equipment/hvac-A109
  ```
  It returns the complete JSON document confirming that `hvac-A109` has been registered with its initial readings and timestamps.

---

## Step 7: Visual Verification in 3D Browser Viewer

### 1. Short Description & Reasoning
* **What:** Open the BuildSim 3D interface in a web browser.
* **Why:** Confirm visually that the digital twin engine is actively rendering the LTU building model and that Room A109 is selectable.

### 2. Action
Open your web browser and visit:
👉 **`http://localhost:9090`**

### 3. What You Will See
1. The **3D LTU A-House architectural model** rendered with Three.js / WebGL.
2. In the top navigation bar, click on **Floor 0** (`level0`).
3. Click on room **A109**.
4. The equipment inspector panel will open, displaying:
   * Unit: **`hvac-A109`**
   * Temperature: **`20.5 °C`**
   * $\text{CO}_2$: **`450 ppm`**
   * Setpoint: **`21.0 °C`**
   * Damper: **`1`**

---

## Quick Reference: Restarting or Re-seeding Anytime

If your computer restarts or Docker is rebooted:
```powershell
# 1. Navigate to directory
cd c:\university\staging

# 2. Start containers
docker compose up -d

# 3. Re-seed equipment into BuildSim memory
go run .\scripts\seed_a109.go
```

