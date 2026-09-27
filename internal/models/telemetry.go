package models

import "time"

// TelemetryReading represents a single sensor observation
type TelemetryReading struct {
	Room      string    `json:"room"`
	Level     string    `json:"level"`
	DeviceID  string    `json:"device_id"`
	Type      string    `json:"type"` // "temperature", "co2", "occupancy"
	Value     float64   `json:"value"`
	Unit      string    `json:"unit"`
	Timestamp time.Time `json:"timestamp"`
}

// RoomTelemetrySnapshot represents the consolidated multi-sensor state of a room
type RoomTelemetrySnapshot struct {
	ID          int64     `json:"id,omitempty"`
	Timestamp   time.Time `json:"timestamp"`
	Level       string    `json:"level"`
	Room        string    `json:"room"`
	Temperature float64   `json:"temperature"`
	CO2         float64   `json:"co2"`
	Occupancy   int       `json:"occupancy"`
	PowerW      float64   `json:"power_w"`
}

// ActuatorCommand represents a command dispatched to the actuator service
type ActuatorCommand struct {
	Room     string   `json:"room"`
	Setpoint *float64 `json:"setpoint,omitempty"`
	Damper   *int     `json:"damper,omitempty"`
	Reason   string   `json:"reason,omitempty"`
}

// ActuatorResponse represents the result of validating and applying an actuation command
type ActuatorResponse struct {
	Success   bool      `json:"success"`
	Room      string    `json:"room"`
	Message   string    `json:"message"`
	Timestamp time.Time `json:"timestamp"`
}
