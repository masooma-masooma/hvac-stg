package main

import (
	"testing"
)

func TestCalculatePowerW(t *testing.T) {
	tests := []struct {
		name          string
		setpoint      float64
		damper        int
		temperature   float64
		expectedPower float64
	}{
		{
			name:          "Standby baseline only (no heating lift, damper 0)",
			setpoint:      20.0,
			damper:        0,
			temperature:   20.5, // room is warmer than setpoint -> no heat
			expectedPower: 25.0, // 25W base only
		},
		{
			name:          "Fan power only (damper 2, no heat)",
			setpoint:      20.0,
			damper:        2,
			temperature:   20.0,
			expectedPower: 25.0 + (2.0 * 45.0), // 115W
		},
		{
			name:          "Heating lift 1.0 deg with damper 1",
			setpoint:      21.0,
			damper:        1,
			temperature:   20.0, // 1 deg lift -> 350W heat
			expectedPower: 25.0 + 45.0 + 350.0, // 420W
		},
		{
			name:          "Heating power capped at 2500W max",
			setpoint:      28.0,
			damper:        0,
			temperature:   15.0, // 13 deg lift * 350W = 4550W -> capped at 2500W
			expectedPower: 25.0 + 0.0 + 2500.0, // 2525W
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			power := CalculatePowerW(tt.setpoint, tt.damper, tt.temperature)
			if power != tt.expectedPower {
				t.Errorf("Expected power %.1f W, got %.1f W", tt.expectedPower, power)
			}
		})
	}
}

