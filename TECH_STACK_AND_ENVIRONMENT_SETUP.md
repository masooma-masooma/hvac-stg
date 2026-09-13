# D7065E: Technology Stack & Windows 11 Environment Setup Guide

**Project:** Smart HVAC & Climate Optimization System  
**Course:** D7065E — Embedded Intelligence at the Edge (LTU, 7.5 ECTS)  
**Author:** Masooma Masooma  
**Target Environment:** Windows 11 (64-bit)  

---

## 1. Language Strategy: Why Go + Python (Polyglot Architecture)?

In accordance with the official course assignment specification and the Grade-5 worked example ([`final_report_example`](file:///c:/university/staging/D7065E_PROJECT_REFERENCE.md#41-required-c4-diagrams-using-d2)), this project uses a **polyglot microservice architecture**:

```
┌─────────────────────────────────────────────────────────────┐
│  GO (GOLANG) — High-Performance Edge Layer                  │
│  • IoT Sensor Gateways (Temperature, CO2, Occupancy)        │
│  • Actuator Controllers (HVAC Setpoint, Ventilation Damper) │
│  • Physical Thermodynamics Simulation Engine                │
└──────────────────────────────┬──────────────────────────────┘
                               │  MQTT / REST
┌──────────────────────────────▼──────────────────────────────┐
│  PYTHON — Brain, Math & Analytics Layer                     │
│  • Telemetry Ingestion Pipeline & Data Quality Monitor      │
│  • Model Predictive Control (MPC) Optimizer (scipy/numpy)   │
│  • Parallel Fixed-Schedule Energy Baseline Comparator       │
│  • Historical Parquet Archiver                              │
└─────────────────────────────────────────────────────────────┘
```

### Architectural Justification Matrix

| Microservice Component | Language | Rationale & Trade-off Justification |
| :--- | :---: | :--- |
| **Physical Simulator** | **Go** | Runs an ODE loop every second. Compiles to a static binary with near-zero CPU/RAM overhead (< 10 MB). |
| **Sensor Gateways** | **Go** | Polls BuildSim REST APIs and publishes MQTT packets concurrently. Handles high event frequency without garbage collection pauses. |
| **Actuator Controllers**| **Go** | Enforces dwell-time limits, Simplex safety clamps, and BuildSim state updates. Guarantees fast failure recovery. |
| **Data Pipeline & Store**| **Python** | Consumes MQTT streams, cleans noisy sensor signals, checks rolling variance, and batches into hot database / Parquet cold store. |
| **Autonomous Controller**| **Python** | Solves the non-linear Model Predictive Control (MPC) optimization using `scipy.optimize` and `numpy`. Go lacks scientific ML/optimization ecosystems. |
| **Dashboard** | **Web / Python** | Real-time WebSocket streaming of live temperatures, comfort bounds, and continuous kWh energy savings. |

---

## 2. Windows 11 Pre-Flight System Audit

A live hardware/software audit was performed on this machine:

| Software / Tool | Current Status on Machine | Installed Version & Location |
| :--- | :---: | :--- |
| **Operating System** |  **Ready** | Windows 11 (64-bit) |
| **Go (Golang)** |  **Installed** | **Go 1.27.1** (`C:\Program Files\Go\bin\go.exe`) *(Needs PATH update)* |
| **Python** |  **Installed** | **Python 3.15** (`C:\Program Files\Python315\python.exe`) |
| **Git** |  **Installed** | **Git 2.53.0** (`C:\Program Files\Git\cmd\git.exe`) |
| **Node.js** |  **Installed** | Node.js runtime (`C:\Program Files\nodejs\node.exe`) |
| **WSL 2** |  **Installed** | WSL2 Ubuntu distribution active |
| **Docker Desktop** | ⚠️ **Not Found** | **Action Required:** Download and install Docker Desktop |
| **D2 (Diagrams as Code)**| ⚠️ **Not Found** | **Action Required:** Install via Go command |

---

## 3. Step-by-Step Installation & Setup Guide for Windows 11

Follow these steps before starting development:

### Step 1: Add Go to Windows PATH Environment Variable
Go is already installed on your system, but Windows needs to recognize the `go` command in any terminal:
1. Press `Win + S` and search for **"Edit the system environment variables"**.
2. Click **Environment Variables...** at the bottom right.
3. Under **User variables for qa** (or System variables), select `Path` and click **Edit**.
4. Click **New** and paste:
   ```text
   C:\Program Files\Go\bin
   ```
5. Click **OK** on all dialogs.
6. Open a new PowerShell window and verify:
   ```powershell
   go version
   # Output: go version go1.27.1 windows/amd64
   ```

---

### Step 2: Install Docker Desktop for Windows (Crucial)
All microservices, BuildSim, the MQTT broker, and the database run in Docker containers.
1. Download **Docker Desktop for Windows**:
   👉 [Download Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)
2. Run the installer (`Docker Desktop Installer.exe`).
3. During installation, make sure **"Use WSL 2 instead of Hyper-V (recommended)"** is checked.
4. Restart your computer if prompted.
5. Launch Docker Desktop and wait until the whale icon in the taskbar shows green (running).
6. Verify in PowerShell:
   ```powershell
   docker --version
   docker compose version
   ```

---

### Step 3: Install D2 (C4 Architecture Diagrams as Code)
The course mandates D2 for rendering all C4 architecture diagrams (`context.d2`, `container.d2`, `component.d2`).
1. With Go in your PATH, run:
   ```powershell
   go install oss.terrastruct.com/d2@latest
   ```
2. Make sure your Go binary folder is in your PATH:
   Add `%USERPROFILE%\go\bin` to your `Path` environment variable.
3. Verify:
   ```powershell
   d2 --version
   ```

---

### Step 4: Set Up the Python Virtual Environment
For the pipeline and MPC controller:
1. Navigate to your project directory in PowerShell:
   ```powershell
   cd c:\university\staging
   ```
2. Create a virtual environment:
   ```powershell
   "C:\Program Files\Python315\python.exe" -m venv .venv
   ```
3. Activate the virtual environment:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   *(If you get a script execution policy error, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once).*
4. Install the core project dependencies:
   ```powershell
   pip install paho-mqtt scipy numpy pandas pyarrow duckdb requests pymupdf
   ```

---

### Step 5: Recommended VS Code Extensions
If you use Visual Studio Code, install these extensions for smooth pair programming:
* **Go** (`golang.Go`) — IntelliSense, syntax checking, and formatting.
* **Python** (`ms-python.python`) — Linting and interactive debugging.
* **Docker** (`ms-azuretools.vscode-docker`) — Manage containers, images, and inspect logs directly.
* **D2** (`terrastruct.d2`) — Live preview for C4 architecture diagrams.

---

## 4. Verification: How to Confirm You Are 100% Ready

Run these 5 checks in a fresh terminal:

```powershell
# 1. Check Go
go version

# 2. Check Python
python --version

# 3. Check Docker
docker compose version

# 4. Check Git
git --version

# 5. Check D2 Diagram Tool
d2 --version
```

If all 5 commands output version numbers without errors, your Windows 11 machine is ready for implementation.

---

## 5. Live Links to Local Project References

* [**`D7065E_PROJECT_REFERENCE.md`**](file:///c:/university/staging/D7065E_PROJECT_REFERENCE.md) — Comprehensive technical reference: physics ODEs, C4 diagrams, BuildSim API reference, and fault-injection matrix.
* [**`PROPOSAL_IMPROVEMENTS.md`**](file:///c:/university/staging/PROPOSAL_IMPROVEMENTS.md) — The 4 specific weakness points and replacement text for your project proposal.
* [**`notes/links.txt`**](file:///c:/university/staging/notes/links.txt) — Official course repository URLs and tutorial links.
* [**`notes/smart HVAC proposal.pdf`**](file:///c:/university/staging/notes/smart%20HVAC%20proposal.pdf) — Original project proposal document.
* [**`notes/D7065E_Assignment_Specification.pdf`**](file:///c:/university/staging/notes/D7065E_Assignment_Specification.pdf) — Course assignment specification and grading rubric.

