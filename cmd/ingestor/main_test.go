package main

import (
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"hvac/internal/models"
)

func TestCalculatePowerW(t *testing.T) {
	tests := []struct {
		name          string
		setpoint      float64
		damper        int
		temperature   float64
		occupancy     int
		expectedPower float64
	}{
		{
			name:          "Holding 21°C, damper 1: heater replaces envelope + ventilation loss",
			setpoint:      21.0,
			damper:        1,
			temperature:   21.0,
			expectedPower: 25 + 45 + (0.005+0.008)*9*8750, // 1093.75 W
		},
		{
			name:          "Eco setback 18°C, damper 0",
			setpoint:      18.0,
			damper:        0,
			temperature:   18.0,
			expectedPower: 25 + 0.005*6*8750, // 287.5 W
		},
		{
			name:          "Heating lift 1°C below setpoint adds 350 W",
			setpoint:      21.0,
			damper:        0,
			temperature:   20.0,
			expectedPower: 25 + 350 + 0.005*8*8750, // 725 W
		},
		{
			name:          "Occupant heat covers the losses: no heating, fan only",
			setpoint:      21.0,
			damper:        1,
			temperature:   21.0,
			occupancy:     25,
			expectedPower: 25 + 45, // gain 0.25 > loss 0.117
		},
		{
			name:          "Heating power capped at 2500 W",
			setpoint:      28.0,
			damper:        0,
			temperature:   15.0,
			expectedPower: 25 + 2500,
		},
		{
			name:          "Room warmer than setpoint: radiator cannot cool",
			setpoint:      18.0,
			damper:        3,
			temperature:   24.0,
			expectedPower: 25 + 135, // compensation faded out: radiator off
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			power := CalculatePowerW(tt.setpoint, tt.damper, tt.temperature, tt.occupancy)
			if math.Abs(power-tt.expectedPower) > 1e-6 {
				t.Errorf("Expected power %.3f W, got %.3f W", tt.expectedPower, power)
			}
		})
	}
}

func reading(typ string, value float64, ts time.Time) models.TelemetryReading {
	return models.TelemetryReading{Room: "A109", Level: "level0", Type: typ, Value: value, Timestamp: ts}
}

func TestValidateSnapshot(t *testing.T) {
	now := time.Now().UTC()
	tests := []struct {
		name     string
		readings []models.TelemetryReading
		wantErr  string
	}{
		{"valid bundle", []models.TelemetryReading{reading("temperature", 21, now), reading("co2", 600, now), reading("occupancy", 4, now)}, ""},
		{"missing CO2 reading", []models.TelemetryReading{reading("temperature", 21, now), reading("occupancy", 4, now)}, "missing co2"},
		{"temperature too high", []models.TelemetryReading{reading("temperature", 75, now), reading("co2", 600, now), reading("occupancy", 0, now)}, "temperature"},
		{"temperature too low", []models.TelemetryReading{reading("temperature", -40, now), reading("co2", 600, now), reading("occupancy", 0, now)}, "temperature"},
		{"CO2 below outdoor physics", []models.TelemetryReading{reading("temperature", 21, now), reading("co2", 120, now), reading("occupancy", 0, now)}, "CO2"},
		{"negative occupancy", []models.TelemetryReading{reading("temperature", 21, now), reading("co2", 600, now), reading("occupancy", -3, now)}, "occupancy"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			snap, seen := BuildSnapshot(tt.readings)
			err := ValidateSnapshot(snap, seen)
			if tt.wantErr == "" && err != nil {
				t.Fatalf("expected valid snapshot, got %v", err)
			}
			if tt.wantErr != "" && (err == nil || !strings.Contains(err.Error(), tt.wantErr)) {
				t.Fatalf("expected error containing %q, got %v", tt.wantErr, err)
			}
		})
	}
}

func TestBuildSnapshotUsesOldestTimestamp(t *testing.T) {
	newer := time.Date(2026, 10, 6, 10, 0, 5, 0, time.UTC)
	older := time.Date(2026, 10, 6, 10, 0, 1, 0, time.UTC)
	snap, _ := BuildSnapshot([]models.TelemetryReading{
		reading("temperature", 21, newer), reading("co2", 600, older), reading("occupancy", 0, newer),
	})
	if !snap.Timestamp.Equal(older) {
		t.Fatalf("expected oldest timestamp %v, got %v", older, snap.Timestamp)
	}
}

func TestCurrentActuatorStateReadsBuildSim(t *testing.T) {
	setpoint := "19.5"
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/actuators/A109-setpoint":
			w.Write([]byte(`{"id":"A109-setpoint","state":"` + setpoint + `"}`))
		case "/api/actuators/A109-damper":
			w.Write([]byte(`{"id":"A109-damper","state":"3"}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer ts.Close()

	svc := &IngestorService{buildsimURL: ts.URL, httpClient: ts.Client(), actuators: map[string]actuatorState{}}

	sp, d, ok := svc.currentActuatorState("A109")
	if !ok || sp != 19.5 || d != 3 {
		t.Fatalf("expected live state 19.5/3, got %.1f/%d ok=%v", sp, d, ok)
	}

	// Actuator change must be picked up on the next packet (no static cache).
	setpoint = "22.0"
	sp, _, _ = svc.currentActuatorState("A109")
	if sp != 22.0 {
		t.Fatalf("expected updated setpoint 22.0, got %.1f", sp)
	}

	// BuildSim outage: fall back to the recent cached read.
	ts.Close()
	sp, d, ok = svc.currentActuatorState("A109")
	if !ok || sp != 22.0 || d != 3 {
		t.Fatalf("expected cached state 22.0/3 during outage, got %.1f/%d ok=%v", sp, d, ok)
	}
}
