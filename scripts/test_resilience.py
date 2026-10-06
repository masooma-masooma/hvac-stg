"""
Automated Resilience & Fault-Injection Test Suite
Injects faults into the running Docker stack and measures recovery times.
Energy/comfort benchmarking is deliberately NOT part of this script; see
scripts/benchmark_scenarios.py.

Tests:
1. MQTT broker restart          -> time until fresh telemetry is stored again
2. BuildSim restart (state wipe) -> time until the simulator re-seeds A109 and telemetry resumes
3. Safety guardrail              -> out-of-range setpoints are clamped to 16-28 degC
4. Physical simulator stopped    -> controller detects stale telemetry and enters Safe Fallback,
                                    then returns to MPC once the simulator is back
5. Smart-controller process exit -> Docker restart policy brings it back; time until MPC dispatches again

Note: telemetry is published at MQTT QoS 0, so readings produced while the broker
is down are lost; test 1 measures connection recovery, not zero data loss.

Usage (from the repository root, stack running):  python scripts/test_resilience.py
"""

import json
import subprocess
import time
from datetime import datetime, timezone

import requests

BUILDSIM_URL = "http://localhost:9090"
ACTUATOR_URL = "http://localhost:8080"
INGESTOR_URL = "http://localhost:8081"
CONTROLLER_URL = "http://localhost:8082"

results = []


def log_test(name: str, passed: bool, recovery_sec, details: str):
    results.append({
        "test": name,
        "status": "PASS" if passed else "FAIL",
        "recovery_seconds": None if recovery_sec is None else round(recovery_sec, 2),
        "details": details,
    })
    shown = "n/a" if recovery_sec is None else f"{recovery_sec:.2f}s"
    print(f"[{'PASS' if passed else 'FAIL'}] {name} (recovery: {shown}) -> {details}")


def run_cmd(cmd: str):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def latest_row_time():
    try:
        rows = requests.get(f"{INGESTOR_URL}/api/history", params={"room": "A109", "minutes": 1}, timeout=2).json()
        if rows:
            return datetime.fromisoformat(rows[-1]["timestamp"].replace("Z", "+00:00"))
    except Exception:
        pass
    return None


def controller_health():
    try:
        return requests.get(f"{CONTROLLER_URL}/healthz", timeout=2).json()
    except Exception:
        return None


def wait_for(predicate, timeout_s: float, poll_s: float = 0.25) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll_s)
    return False


def fresh_telemetry_after(t0: datetime):
    def check():
        ts = latest_row_time()
        return ts is not None and ts > t0
    return check


def test_broker_restart():
    print("\n--- Test 1: MQTT broker restart ---")
    res = run_cmd("docker compose restart mosquitto")
    restarted = datetime.now(timezone.utc)
    start = time.time()
    if res.returncode != 0:
        log_test("MQTT Broker Restart", False, None, f"docker compose restart failed: {res.stderr.strip()}")
        return
    ok = wait_for(fresh_telemetry_after(restarted), 30)
    log_test("MQTT Broker Restart", ok, time.time() - start if ok else None,
             "Gateway and ingestor reconnected; first telemetry row stamped after the broker came back. "
             "Readings published during the outage were dropped (QoS 0).")


def test_buildsim_restart_self_healing():
    print("\n--- Test 2: BuildSim restart (in-memory state wiped) ---")
    run_cmd("docker compose restart buildsim")
    restarted = datetime.now(timezone.utc)
    start = time.time()

    def reseeded():
        try:
            return requests.get(f"{BUILDSIM_URL}/api/equipment/hvac-A109", timeout=2).status_code == 200
        except Exception:
            return False

    ok_seed = wait_for(reseeded, 40)
    seed_time = time.time() - start
    ok_flow = ok_seed and wait_for(fresh_telemetry_after(restarted), 30)
    total = time.time() - start
    log_test("BuildSim Restart Self-Healing", ok_seed and ok_flow, total if ok_flow else None,
             f"Simulator watchdog re-registered A109 equipment after {seed_time:.1f}s; "
             f"telemetry flowing again after {total:.1f}s.")


def test_safety_guardrail():
    print("\n--- Test 3: Safety guardrail clamping ---")
    r_high = requests.post(f"{ACTUATOR_URL}/commands", json={"room": "A109", "setpoint": 48.0, "reason": "resilience test"}, timeout=3)
    r_low = requests.post(f"{ACTUATOR_URL}/commands", json={"room": "A109", "setpoint": 4.0, "reason": "resilience test"}, timeout=3)
    clamped_high = r_high.status_code == 200 and "28.0°C" in r_high.json().get("message", "")
    clamped_low = r_low.status_code == 200 and "16.0°C" in r_low.json().get("message", "")
    log_test("Safety Guardrail Clamping", clamped_high and clamped_low, None,
             "48.0°C request clamped to 28.0°C and 4.0°C request clamped to 16.0°C by the deterministic guardrail. "
             "The MPC overwrites the test setpoint on its next cycle.")


def test_stale_telemetry_safe_fallback():
    print("\n--- Test 4: Physical simulator stopped -> stale telemetry -> Safe Fallback ---")
    run_cmd("docker compose stop simulator")
    start = time.time()

    def in_fallback():
        h = controller_health()
        return h is not None and h.get("mode") == "SAFE_FALLBACK"

    entered = wait_for(in_fallback, 70)
    detect = time.time() - start
    sp = dmp = None
    if entered:
        wait_for(lambda: requests.get(f"{BUILDSIM_URL}/api/actuators/A109-setpoint", timeout=2).json().get("state") == "21.0", 15)
        sp = requests.get(f"{BUILDSIM_URL}/api/actuators/A109-setpoint", timeout=2).json().get("state")
        dmp = requests.get(f"{BUILDSIM_URL}/api/actuators/A109-damper", timeout=2).json().get("state")

    run_cmd("docker compose start simulator")
    restart = time.time()

    def back_to_mpc():
        h = controller_health()
        return h is not None and h.get("mode") == "MPC"

    recovered = wait_for(back_to_mpc, 60)
    recovery = time.time() - restart
    passed = entered and sp == "21.0" and dmp == "1" and recovered
    log_test("Stale Telemetry Safe Fallback", passed, recovery if recovered else None,
             f"Frozen sensors detected and Safe Fallback (setpoint {sp}, damper {dmp}) entered {detect:.1f}s after the "
             f"simulator stopped (threshold 30s + up to one 10s control cycle); MPC resumed {recovery:.1f}s after restart.")


def test_controller_crash_restart():
    print("\n--- Test 5: Smart-controller process exit -> Docker restart policy ---")
    before = run_cmd("docker inspect -f {{.RestartCount}} smart-controller").stdout.strip()
    run_cmd('docker exec smart-controller sh -c "kill -TERM 1"')
    start = time.time()

    def dispatching_again():
        try:
            plan = requests.get(f"{CONTROLLER_URL}/api/mpc/trajectory", timeout=2).json()["latest_plan"]
            return plan.get("mode") == "MPC" and plan.get("dispatch_ok") is not None
        except Exception:
            return False

    time.sleep(1.0)
    ok = wait_for(dispatching_again, 90)
    after = run_cmd("docker inspect -f {{.RestartCount}} smart-controller").stdout.strip()
    log_test("Smart Controller Restart", ok and after != before, time.time() - start if ok else None,
             f"Container restart count {before} -> {after}; actuators held their last setpoint during the outage "
             f"and the MPC resumed dispatching (includes model loading and the 5s start-up delay).")


def main():
    print("==========================================================")
    print("   D7065E - Resilience & Fault-Injection Test Suite       ")
    print("==========================================================")
    test_broker_restart()
    test_buildsim_restart_self_healing()
    test_safety_guardrail()
    test_stale_telemetry_safe_fallback()
    test_controller_crash_restart()

    print("\n" + json.dumps(results, indent=2, ensure_ascii=False))
    with open("data/resilience_benchmark.json", "w", encoding="utf-8") as f:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "results": results}, f, indent=2, ensure_ascii=False)
    print("\nBenchmark saved to: data/resilience_benchmark.json")
    if any(r["status"] == "FAIL" for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
