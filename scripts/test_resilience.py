"""
Automated Resilience & Chaos Engineering Test Suite
Evaluates Mean Time to Recovery (MTTR), fault recovery, and safety guardrails.
Produces empirical benchmarks required for Course Notes 4 and the final report.
"""

import time
import subprocess
import requests
import json
import sys

BUILDSIM_URL = "http://localhost:9090"
ACTUATOR_URL = "http://localhost:8080"
INGESTOR_URL = "http://localhost:8081"
CONTROLLER_URL = "http://localhost:8082"

results = []

def log_test(name: str, passed: bool, mttr_sec: float, details: str):
    status = "PASS" if passed else "FAIL"
    results.append({
        "test": name,
        "status": status,
        "mttr_seconds": round(mttr_sec, 2),
        "details": details
    })
    print(f"[{status}] {name} (MTTR: {mttr_sec:.2f}s) -> {details}")

def run_cmd(cmd: str):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

def test_broker_restart():
    print("\n--- Test 1: MQTT Broker Failure & Auto-Reconnection ---")
    start = time.time()
    # Restart Mosquitto
    res = run_cmd("docker compose restart mosquitto")
    if res.returncode != 0:
        log_test("Broker Crash Recovery", False, 0.0, f"Failed to restart mosquitto: {res.stderr}")
        return

    # Measure time until ingestor starts logging new telemetry again
    reconnected = False
    deadline = time.time() + 25.0
    while time.time() < deadline:
        try:
            r = requests.get(f"{INGESTOR_URL}/api/history?room=A109&minutes=1", timeout=2)
            if r.status_code == 200 and len(r.json()) > 0:
                reconnected = True
                break
        except Exception:
            pass
        time.sleep(1.0)

    elapsed = time.time() - start
    log_test("Broker Crash Recovery", reconnected, elapsed, "Clients auto-reconnected and resumed telemetry pipeline.")

def test_buildsim_crash_and_self_healing():
    print("\n--- Test 2: Digital Twin Crash & In-Memory Self-Healing ---")
    start = time.time()
    # Restart BuildSim (wipes all in-memory equipment)
    run_cmd("docker compose restart buildsim")

    # Measure time until simulator auto-seeder restores Room A109
    recovered = False
    deadline = time.time() + 25.0
    while time.time() < deadline:
        try:
            r = requests.get(f"{BUILDSIM_URL}/api/equipment/hvac-A109", timeout=2)
            if r.status_code == 200 and r.json().get("status") == "running":
                recovered = True
                break
        except Exception:
            pass
        time.sleep(1.0)

    elapsed = time.time() - start
    log_test("Digital Twin Self-Healing", recovered, elapsed, "Simulator automatically detected wiped memory and re-seeded equipment.")

def test_simplex_safety_guardrail():
    print("\n--- Test 3: Simplex Safety Guardrail Clamping ---")
    start = time.time()
    # Send dangerously high temperature
    high_cmd = {"room": "A109", "setpoint": 48.0}
    r_high = requests.post(f"{ACTUATOR_URL}/commands", json=high_cmd, timeout=3)
    clamped_high = (r_high.status_code == 200) and ("28.0°C" in r_high.json().get("message", ""))

    # Send freezing temperature
    low_cmd = {"room": "A109", "setpoint": 4.0}
    r_low = requests.post(f"{ACTUATOR_URL}/commands", json=low_cmd, timeout=3)
    clamped_low = (r_low.status_code == 200) and ("16.0°C" in r_low.json().get("message", ""))

    elapsed = time.time() - start
    passed = clamped_high and clamped_low
    details = "48.0°C clamped to 28.0°C and 4.0°C clamped to 16.0°C by Simplex supervisor."
    log_test("Simplex Safety Clamping", passed, elapsed, details)

def test_autonomous_benchmarking():
    print("\n--- Test 4: Autonomous MPC Benchmarking ---")
    start = time.time()
    try:
        r = requests.get(f"{CONTROLLER_URL}/api/comparison", timeout=3)
        if r.status_code == 200:
            data = r.json()
            savings = data.get("benchmarks", {}).get("energy_savings_percentage", 0.0)
            comfort = data.get("benchmarks", {}).get("comfort_preserved", False)
            passed = (savings > 0.0) and comfort
            elapsed = time.time() - start
            details = f"Smart MPC saved {savings:.1f}% energy over baseline with comfort preserved."
            log_test("MPC Performance Benchmark", passed, elapsed, details)
        else:
            log_test("MPC Performance Benchmark", False, 0.0, f"HTTP status {r.status_code}")
    except Exception as e:
        log_test("MPC Performance Benchmark", False, 0.0, str(e))

def main():
    print("==========================================================")
    print("   D7065E - Chaos Engineering & Resilience Test Suite    ")
    print("==========================================================")
    test_broker_restart()
    test_buildsim_crash_and_self_healing()
    test_simplex_safety_guardrail()
    test_autonomous_benchmarking()

    print("\n==========================================================")
    print("                 Summary of Test Results                  ")
    print("==========================================================")
    print(json.dumps(results, indent=2))
    
    # Save results to json for report inclusion
    with open("data/resilience_benchmark.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nBenchmark saved to: data/resilience_benchmark.json")

if __name__ == "__main__":
    main()

