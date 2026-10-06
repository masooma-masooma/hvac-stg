"""
Physical Thermodynamic & IAQ Dynamics Model of Room A109.

This module is the single Python source of truth for the room physics. The Go
physical-simulator (cmd/simulator) and the ingestor power model (cmd/ingestor)
implement exactly the same equations and constants, so that the fixed-schedule
baseline twin, the Model 02 training corpus and the live room agree.

All rates are per simulated second; the Go simulator integrates them with a
1-second explicit Euler tick and this module does the same. The dynamics are
time-compressed (minutes instead of hours) so that a lab demo shows visible
reactions; this is stated as a limitation in the report.

Thermal balance (degC/s):
    dT/dt = heater - (K_ENVELOPE + K_VENT_THERMAL * damper) * (T - T_out) + K_OCC_HEAT * occupancy
The radiator has a local thermostat that compensates losses and gains, so the
room converges to the setpoint without steady-state offset when heating is
possible. Above the setpoint the compensation fades out over THERMOSTAT_BAND, so
the radiator switches off; it cannot cool, an overheated room is cooled only by
ventilation and the envelope.

CO2 balance (ppm/s):
    dC/dt = CO2_GEN * occupancy - (VENT_CO2_BASE + VENT_CO2_PER_LEVEL * damper) * (C - C_out)

Electrical power (W):
    P = BASE_W + FAN_W_PER_LEVEL * damper + W_PER_RATE * heater
"""

from typing import Tuple

import numpy as np

AMBIENT_TEMP = 12.0          # outdoor temperature, degC
AMBIENT_CO2 = 420.0          # outdoor CO2, ppm

K_HVAC = 0.04                # 1/s, closed-loop response of the radiator thermostat
K_ENVELOPE = 0.005           # 1/s, conductive loss through the envelope
K_VENT_THERMAL = 0.008       # 1/s per damper level, heat carried out with ventilation air
                             # (damper 3 ~ 175 L/s, sized for ~25 people at 7 L/s each)
K_OCC_HEAT = 0.01            # degC/s per occupant (~90 W sensible heat)

CO2_GEN = 1.5                # ppm/s per occupant
VENT_CO2_BASE = 0.01         # 1/s infiltration with damper closed
VENT_CO2_PER_LEVEL = 0.02    # 1/s per damper level

BASE_W = 25.0                # standby electronics
FAN_W_PER_LEVEL = 45.0       # ventilation fan per damper level
W_PER_RATE = 8750.0          # W per degC/s of heating (= 350 W per degC of lift at K_HVAC)
MAX_HEATER_W = 2500.0
MAX_HEATER_RATE = MAX_HEATER_W / W_PER_RATE

THERMOSTAT_BAND = 0.5        # degC above setpoint over which loss compensation fades out

COMFORT_MIN = 20.0           # ASHRAE 55 / EN 16798 band used for evaluation
COMFORT_MAX = 24.0
CO2_LIMIT = 1000.0


def heater_rate(temp, setpoint, damper, occupancy, ambient_temp=AMBIENT_TEMP):
    """Heating delivered by the radiator thermostat (degC/s), works on scalars and numpy arrays."""
    loss = (K_ENVELOPE + K_VENT_THERMAL * damper) * (temp - ambient_temp)
    gain = K_OCC_HEAT * occupancy
    compensation = np.clip(1.0 - (temp - setpoint) / THERMOSTAT_BAND, 0.0, 1.0)
    demand = K_HVAC * (setpoint - temp) + compensation * (loss - gain)
    return np.clip(demand, 0.0, MAX_HEATER_RATE)


def power_w(temp, setpoint, damper, occupancy, ambient_temp=AMBIENT_TEMP):
    """Instantaneous electrical power draw (W)."""
    return BASE_W + FAN_W_PER_LEVEL * damper + W_PER_RATE * heater_rate(temp, setpoint, damper, occupancy, ambient_temp)


def euler_tick(temp, co2, setpoint, damper, occupancy,
               ambient_temp=AMBIENT_TEMP, ambient_co2=AMBIENT_CO2, dt=1.0):
    """Advance the room by one tick of dt seconds. Returns (temp, co2, power_w)."""
    heat = heater_rate(temp, setpoint, damper, occupancy, ambient_temp)
    loss = (K_ENVELOPE + K_VENT_THERMAL * damper) * (temp - ambient_temp)
    gain = K_OCC_HEAT * occupancy
    new_temp = temp + (heat - loss + gain) * dt

    vent = VENT_CO2_BASE + VENT_CO2_PER_LEVEL * damper
    new_co2 = co2 + (CO2_GEN * occupancy - vent * (co2 - ambient_co2)) * dt
    new_co2 = np.maximum(new_co2, ambient_co2)

    power = BASE_W + FAN_W_PER_LEVEL * damper + W_PER_RATE * heat
    return new_temp, new_co2, power


class BuildingDynamics:
    def __init__(self,
                 ambient_temp: float = AMBIENT_TEMP,
                 ambient_co2: float = AMBIENT_CO2,
                 room_volume_m3: float = 75.0):
        self.ambient_temp = ambient_temp
        self.ambient_co2 = ambient_co2
        self.room_volume = room_volume_m3

    def step(self,
             temp: float,
             co2: float,
             setpoint: float,
             damper: int,
             occupancy: int,
             dt_seconds: float = 60.0) -> Tuple[float, float, float]:
        """
        Simulate dt_seconds with 1-second Euler ticks (identical to the Go simulator).
        Returns: (new_temp, new_co2, average_power_watts_over_the_interval)
        """
        n_full = int(dt_seconds)
        remainder = dt_seconds - n_full
        energy_ws = 0.0
        t, c = temp, co2
        for _ in range(n_full):
            t, c, p = euler_tick(t, c, setpoint, damper, occupancy, self.ambient_temp, self.ambient_co2, 1.0)
            energy_ws += p
        if remainder > 1e-9:
            t, c, p = euler_tick(t, c, setpoint, damper, occupancy, self.ambient_temp, self.ambient_co2, remainder)
            energy_ws += p * remainder
        avg_power = energy_ws / dt_seconds if dt_seconds > 0 else float(power_w(temp, setpoint, damper, occupancy, self.ambient_temp))
        return float(t), float(c), float(avg_power)

    def comfort_penalty(self, temp: float, co2: float) -> Tuple[float, float]:
        """
        Squared violation of the ASHRAE 55 comfort band (20-24 degC) and the
        1000 ppm CO2 limit (penalty in units of (100 ppm)^2).
        """
        temp_penalty = 0.0
        if temp < COMFORT_MIN:
            temp_penalty = (COMFORT_MIN - temp) ** 2
        elif temp > COMFORT_MAX:
            temp_penalty = (temp - COMFORT_MAX) ** 2

        co2_penalty = 0.0
        if co2 > CO2_LIMIT:
            co2_penalty = ((co2 - CO2_LIMIT) / 100.0) ** 2

        return temp_penalty, co2_penalty
