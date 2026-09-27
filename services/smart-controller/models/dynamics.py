"""
Physical Thermodynamic & IAQ Dynamics Model
Used by the MPC Controller for multi-step lookahead horizon simulation.
Formulated according to ASHRAE 55 and EN 16798 standards.
"""

from typing import Tuple

class BuildingDynamics:
    def __init__(self, 
                 ambient_temp: float = 12.0, 
                 ambient_co2: float = 420.0,
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
        Simulate room state transition over dt_seconds.
        Returns: (new_temp, new_co2, instantaneous_power_watts)
        """
        # 1. Thermal ODE:
        # Rate of HVAC heat transfer: ~0.04 per second drift rate scaled by dt
        # Heat loss to outdoor: ~0.005 per second drift rate
        # Occupant heat emission: ~0.03 °C per occupant per second
        rate_hvac = 0.04 * (setpoint - temp)
        rate_loss = 0.005 * (self.ambient_temp - temp)
        rate_occ = 0.03 * float(occupancy)
        
        # Scale to dt (using Euler forward step with sub-stepping for stability)
        sub_steps = max(1, int(dt_seconds / 5.0))
        sub_dt = dt_seconds / sub_steps

        curr_t = temp
        curr_c = co2
        
        for _ in range(sub_steps):
            d_t = (0.04 * (setpoint - curr_t) + 0.005 * (self.ambient_temp - curr_t) + 0.03 * float(occupancy)) * (sub_dt / 5.0)
            curr_t += d_t
            
            # 2. CO2 ODE:
            # Exhalation rate: 1.5 ppm per person per second
            # Damper ventilation dilution: 1% to 7% air change rate per second
            vent_rate = 0.01 + (0.02 * float(damper))
            d_co2_gen = 1.5 * float(occupancy) * (sub_dt / 5.0)
            d_co2_vent = vent_rate * (curr_c - self.ambient_co2) * (sub_dt / 5.0)
            curr_c += (d_co2_gen - d_co2_vent)
            if curr_c < self.ambient_co2:
                curr_c = self.ambient_co2

        # 3. Instantaneous Electrical Power (Watts)
        # Standby electronics (25W) + Ventilation Fan (45W per damper level) + Proportional heating (350W per °C lift)
        base_power = 25.0
        fan_power = float(damper) * 45.0
        heating_power = 0.0
        temp_lift = setpoint - curr_t
        if temp_lift > 0:
            heating_power = min(2500.0, temp_lift * 350.0)
            
        power_w = base_power + fan_power + heating_power
        return curr_t, curr_c, power_w

    def comfort_penalty(self, temp: float, co2: float) -> Tuple[float, float]:
        """
        Calculate penalties for violating ASHRAE 55 comfort bounds:
        Thermal Comfort: 20.0°C <= Temp <= 24.0°C
        Air Quality: CO2 <= 1000 ppm
        """
        temp_penalty = 0.0
        if temp < 20.0:
            temp_penalty = (20.0 - temp) ** 2
        elif temp > 24.0:
            temp_penalty = (temp - 24.0) ** 2

        co2_penalty = 0.0
        if co2 > 1000.0:
            co2_penalty = ((co2 - 1000.0) / 100.0) ** 2

        return temp_penalty, co2_penalty

