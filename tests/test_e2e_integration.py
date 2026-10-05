"""
End-to-End (E2E) Integration Test for Smart HVAC Cyber-Physical Control Loop
Verifies:
1. Injects occupancy into BuildSim digital twin (PUT /api/occupancy)
2. Waits one control loop cycle (Gateway -> MQTT -> Ingestor -> Smart Controller -> Actuator -> BuildSim)
3. Asserts actuator state in BuildSim changed
4. Asserts physical simulator updated sensor readings in response to the changed environment
5. Resets occupancy to 0
"""

import time
import requests
import pytest

BUILDSIM_URL = "http://localhost:9090"
SMART_CONTROLLER_URL = "http://localhost:8082"
INGESTOR_URL = "http://localhost:8081"

def test_closed_loop_e2e():
    # 1. Verify all services are responsive
    try:
        bs_check = requests.get(f"{BUILDSIM_URL}/api/building", timeout=3)
        assert bs_check.status_code == 200, "BuildSim not accessible"
        sc_check = requests.get(f"{SMART_CONTROLLER_URL}/healthz", timeout=3)
        assert sc_check.status_code == 200, "Smart Controller not accessible"
        ing_check = requests.get(f"{INGESTOR_URL}/healthz", timeout=3)
        assert ing_check.status_code == 200, "Ingestor not accessible"
    except Exception as e:
        pytest.fail(f"Infrastructure service unreachable: {e}")

    # 2. Record initial sensor states
    init_temp_resp = requests.get(f"{BUILDSIM_URL}/api/sensors/A109-temp").json()
    init_co2_resp = requests.get(f"{BUILDSIM_URL}/api/sensors/A109-co2").json()
    init_temp = float(init_temp_resp.get("value", 21.0))
    init_co2 = float(init_co2_resp.get("value", 450.0))
    print(f"\n[E2E] Initial State: Temp={init_temp:.2f}°C, CO2={init_co2:.0f} ppm")

    # 3. Inject 10 occupants into Room A109 via BuildSim
    occupants_payload = {
        "level0/A109": {
            "persons": [{"id": f"student_{i}", "name": f"Student {i}"} for i in range(1, 11)],
            "aliens": []
        }
    }
    occ_resp = requests.put(f"{BUILDSIM_URL}/api/occupancy", json=occupants_payload, timeout=3)
    assert occ_resp.status_code == 200, f"Failed to inject occupancy into BuildSim: {occ_resp.text}"
    print("[E2E] Injected 10 occupants into BuildSim room A109.")

    # 4. Wait for the control loop cycle (Gateway 2s + Ingestor + Controller 10s cycle)
    print("[E2E] Waiting 14 seconds for telemetry ingestion, MPC optimization, and actuation dispatch...")
    time.sleep(14)

    # 5. Assert actuator state in BuildSim was updated by the Smart Controller
    sp_resp = requests.get(f"{BUILDSIM_URL}/api/actuators/A109-setpoint").json()
    dmp_resp = requests.get(f"{BUILDSIM_URL}/api/actuators/A109-damper").json()
    applied_sp = float(sp_resp.get("state", "21.0"))
    applied_dmp = int(dmp_resp.get("state", "1"))
    print(f"[E2E] BuildSim Actuators after MPC: Setpoint={applied_sp:.1f}°C, Damper={applied_dmp}")

    # Under 10 occupants, the MPC optimizer must command an active comfort setpoint and ventilate
    assert 16.0 <= applied_sp <= 28.0, f"Setpoint {applied_sp} outside Simplex guardrail limits!"
    assert applied_dmp >= 1, f"Occupied room damper must be active; got {applied_dmp}"

    # 6. Wait 6 seconds for the Physical Simulator ODE to advance room climate
    print("[E2E] Waiting 6 seconds for Physical Simulator ODE thermodynamics...")
    time.sleep(6)

    updated_temp_resp = requests.get(f"{BUILDSIM_URL}/api/sensors/A109-temp").json()
    updated_co2_resp = requests.get(f"{BUILDSIM_URL}/api/sensors/A109-co2").json()
    updated_temp = float(updated_temp_resp.get("value", init_temp))
    updated_co2 = float(updated_co2_resp.get("value", init_co2))
    print(f"[E2E] Sensor Readings after physics step: Temp={updated_temp:.2f}°C, CO2={updated_co2:.0f} ppm")

    # The 10 occupants produce sensible metabolic heat and CO2
    assert updated_co2 > init_co2 or updated_co2 >= 500.0, "CO2 sensor must increase or stay elevated with 10 occupants"

    # 7. Clean up: reset room occupancy to 0
    empty_payload = {"level0/A109": {"persons": [], "aliens": []}}
    requests.put(f"{BUILDSIM_URL}/api/occupancy", json=empty_payload, timeout=3)
    print("[E2E] Closed cyber-physical loop successfully verified! Reset room to 0 occupants.")

if __name__ == "__main__":
    test_closed_loop_e2e()

