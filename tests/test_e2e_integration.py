"""
End-to-End (E2E) Integration Test for the Smart HVAC Cyber-Physical Control Loop
Verifies against the running Docker stack:
1. All services respond (BuildSim, ingestor, smart controller)
2. Injecting 10 occupants into BuildSim makes the MPC (via sensor-gateway -> MQTT ->
   ingestor -> smart-controller -> actuator-controller -> BuildSim) leave eco setback:
   a comfort setpoint and the ventilation turned on
3. The ingestor's power estimate follows the applied actuator state
4. The physical simulator keeps the room inside 20-24 degC and below 1000 ppm CO2
5. Occupancy is always reset to 0 afterwards

Run from the host:  python tests/test_e2e_integration.py   (pytest optional)
Override URLs with BUILDSIM_URL / SMART_CONTROLLER_URL / INGESTOR_URL.
"""

import os
import time

import requests

BUILDSIM_URL = os.getenv("BUILDSIM_URL", "http://localhost:9090")
SMART_CONTROLLER_URL = os.getenv("SMART_CONTROLLER_URL", "http://localhost:8082")
INGESTOR_URL = os.getenv("INGESTOR_URL", "http://localhost:8081")
ROOM_KEY = "level0/A109"


def set_occupancy(n: int):
    payload = {ROOM_KEY: {"persons": [{"id": f"student_{i}", "name": f"Student {i}"} for i in range(1, n + 1)], "aliens": []}}
    resp = requests.put(f"{BUILDSIM_URL}/api/occupancy", json=payload, timeout=3)
    assert resp.status_code == 200, f"Failed to set occupancy in BuildSim: {resp.text}"


def sensor(name: str) -> float:
    return float(requests.get(f"{BUILDSIM_URL}/api/sensors/A109-{name}", timeout=3).json()["value"])


def actuator(name: str) -> float:
    return float(requests.get(f"{BUILDSIM_URL}/api/actuators/A109-{name}", timeout=3).json()["state"])


def wait_until(predicate, timeout_s: float, poll_s: float = 1.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(poll_s)
    return predicate()


def test_closed_loop_e2e():
    for url in (f"{BUILDSIM_URL}/api/building", f"{SMART_CONTROLLER_URL}/healthz", f"{INGESTOR_URL}/healthz"):
        assert requests.get(url, timeout=3).status_code == 200, f"Service not reachable: {url}"
    health = requests.get(f"{SMART_CONTROLLER_URL}/healthz", timeout=3).json()
    assert health["mode"] == "MPC", f"Controller is not in MPC mode before the test: {health}"

    print(f"\n[E2E] Initial: Temp={sensor('temp'):.1f}°C CO2={sensor('co2'):.0f} ppm "
          f"Setpoint={actuator('setpoint'):.1f} Damper={actuator('damper'):.0f}")
    try:
        set_occupancy(10)
        start = time.time()
        print("[E2E] Injected 10 occupants. Waiting for the MPC to react...")

        reacted = wait_until(lambda: actuator("setpoint") >= 20.0 and actuator("damper") >= 1, timeout_s=45)
        sp, dmp = actuator("setpoint"), int(actuator("damper"))
        print(f"[E2E] Actuators after {time.time() - start:.0f}s: Setpoint={sp:.1f}°C Damper={dmp}")
        assert reacted, f"MPC did not leave eco setback for 10 occupants (setpoint {sp}, damper {dmp})"

        reason = requests.get(f"{SMART_CONTROLLER_URL}/api/mpc/trajectory", timeout=3).json()["latest_plan"]["reason"]
        print(f"[E2E] Decision: {reason}")
        assert "occupied (10p)" in reason or "Dwell" in reason

        # The ingestor must price the applied actuator state, not a static default.
        def ingested_state_matches():
            rows = requests.get(f"{INGESTOR_URL}/api/history", params={"room": "A109", "minutes": 1}, timeout=3).json()
            return rows and rows[-1].get("damper") == actuator("damper") and rows[-1].get("setpoint") == actuator("setpoint")
        assert wait_until(ingested_state_matches, timeout_s=10), "Ingestor rows do not carry the applied actuator state"

        # Let the room settle under the MPC's control (CO2 time constant is ~30-50 s).
        time.sleep(60)
        temp, co2 = sensor("temp"), sensor("co2")
        print(f"[E2E] After settling: Temp={temp:.1f}°C CO2={co2:.0f} ppm")
        assert 20.0 <= temp <= 24.0, f"Room left the comfort band with 10 occupants: {temp}°C"
        assert co2 < 1000.0, f"CO2 exceeded 1000 ppm with 10 occupants: {co2} ppm"
        print("[E2E] Closed cyber-physical loop verified.")
    finally:
        set_occupancy(0)
        print("[E2E] Reset Room A109 to 0 occupants.")


if __name__ == "__main__":
    test_closed_loop_e2e()
    print("[PASS] test_closed_loop_e2e")
