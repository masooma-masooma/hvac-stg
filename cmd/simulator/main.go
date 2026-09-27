package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

const (
	defaultBaseURL = "http://127.0.0.1:9090"
	roomKey        = "level0/A109"
	roomName       = "A109"
	ambientTemp    = 12.0  // Outdoor temperature in Celsius
	ambientCO2     = 420.0 // Ambient atmospheric CO2 in ppm
)

type ActuatorState struct {
	ID    string `json:"id"`
	State string `json:"state"`
}

type OccupancyResponse map[string]struct {
	Persons []struct {
		ID   string `json:"id"`
		Name string `json:"name"`
	} `json:"persons"`
}

type RoomLayer struct {
	ID      string             `json:"id"`
	Label   string             `json:"label"`
	Unit    string             `json:"unit"`
	Source  string             `json:"source"`
	Minimum float64            `json:"minimum"`
	Maximum float64            `json:"maximum"`
	Values  map[string]float64 `json:"values"`
}

type SensorDefinition struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Type     string `json:"type"`
	DataType string `json:"data_type"`
	Unit     string `json:"unit"`
	Value    string `json:"value"`
}

type ActuatorDefinition struct {
	ID    string `json:"id"`
	Name  string `json:"name"`
	Type  string `json:"type"`
	State string `json:"state"`
}

type EquipmentDefinition struct {
	ID        string               `json:"id"`
	Name      string               `json:"name"`
	Type      string               `json:"type"`
	Category  string               `json:"category"`
	Level     string               `json:"level"`
	Room      string               `json:"room"`
	Status    string               `json:"status"`
	Sensors   []SensorDefinition   `json:"sensors"`
	Actuators []ActuatorDefinition `json:"actuators"`
}

func main() {
	baseURL := strings.TrimRight(os.Getenv("BUILDSIM_URL"), "/")
	if baseURL == "" {
		baseURL = defaultBaseURL
	}

	client := &http.Client{Timeout: 5 * time.Second}
	log.Printf("[Simulator] Starting Physical Thermal & CO2 Simulator for Room %s...", roomName)
	log.Printf("[Simulator] Connecting to BuildSim at %s", baseURL)

	// Auto-seed equipment on startup if not already registered
	ensureEquipmentRegistered(client, baseURL)

	currentTemp := 20.5
	currentCO2 := 450.0
	tickCount := 0

	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		tickCount++

		// Periodically verify equipment is registered (self-healing after BuildSim restarts)
		if tickCount%15 == 0 {
			ensureEquipmentRegistered(client, baseURL)
		}

		// 1. Read Actuator States (Setpoint and Damper)
		setpoint := readActuatorFloat(client, baseURL, "A109-setpoint", 21.0)
		damperLevel := readActuatorInt(client, baseURL, "A109-damper", 1)

		// 2. Read Occupancy
		occupantCount := readOccupancy(client, baseURL, roomKey)

		// 3. Physical State Updates (Thermodynamic & IAQ Discrete ODE)
		deltaHVAC := 0.04 * (setpoint - currentTemp)
		deltaAmbient := 0.005 * (ambientTemp - currentTemp)
		deltaOccHeat := 0.03 * float64(occupantCount)
		currentTemp += deltaHVAC + deltaAmbient + deltaOccHeat

		ventRate := 0.01 + (0.02 * float64(damperLevel))
		deltaCO2Gen := 1.5 * float64(occupantCount)
		deltaCO2Vent := ventRate * (currentCO2 - ambientCO2)
		currentCO2 += deltaCO2Gen - deltaCO2Vent
		if currentCO2 < ambientCO2 {
			currentCO2 = ambientCO2
		}

		// 4. Update BuildSim Sensors every 1 second
		_ = writeSensor(client, baseURL, "A109-temp", fmt.Sprintf("%.1f", currentTemp))
		_ = writeSensor(client, baseURL, "A109-co2", fmt.Sprintf("%.0f", currentCO2))
		_ = writeSensor(client, baseURL, "A109-occ", fmt.Sprintf("%d", occupantCount))

		// 5. Update 3D Heatmap and log status every 5 seconds
		if tickCount%5 == 0 {
			updateHeatmap(client, baseURL, currentTemp)
			log.Printf("[Simulator] Room %s | Temp: %.1f°C (Target: %.1f°C) | CO2: %.0f ppm (Damper: %d) | Occupants: %d",
				roomName, currentTemp, setpoint, currentCO2, damperLevel, occupantCount)
		}
	}
}

// ensureEquipmentRegistered checks if Room A109 equipment exists in BuildSim; if missing, it registers it automatically.
func ensureEquipmentRegistered(client *http.Client, baseURL string) {
	resp, err := client.Get(fmt.Sprintf("%s/api/equipment/hvac-A109", baseURL))
	if err == nil && resp.StatusCode == http.StatusOK {
		resp.Body.Close()
		return // Equipment already exists
	}
	if resp != nil && resp.Body != nil {
		resp.Body.Close()
	}

	log.Printf("[Simulator] Room A109 equipment not found in BuildSim. Auto-seeding...")
	equipment := []EquipmentDefinition{
		{
			ID:       "hvac-A109",
			Name:     "HVAC Unit A109",
			Type:     "ac_unit",
			Category: "hvac",
			Level:    "level0",
			Room:     "A109",
			Status:   "running",
			Sensors: []SensorDefinition{
				{ID: "A109-temp", Name: "Indoor Temperature", Type: "temperature", DataType: "text", Unit: "°C", Value: "20.5"},
				{ID: "A109-co2", Name: "Indoor CO2 Concentration", Type: "co2", DataType: "text", Unit: "ppm", Value: "450"},
				{ID: "A109-occ", Name: "Room Occupancy", Type: "occupancy", DataType: "text", Unit: "persons", Value: "0"},
			},
			Actuators: []ActuatorDefinition{
				{ID: "A109-setpoint", Name: "Heating Setpoint", Type: "setpoint", State: "21.0"},
				{ID: "A109-damper", Name: "Ventilation Damper Level", Type: "fan_speed", State: "1"},
			},
		},
	}

	data, err := json.Marshal(equipment)
	if err != nil {
		log.Printf("[Simulator] JSON marshal error: %v", err)
		return
	}

	res, postErr := client.Post(fmt.Sprintf("%s/api/equipment/bulk", baseURL), "application/json", bytes.NewBuffer(data))
	if postErr != nil {
		log.Printf("[Simulator] Auto-seeding failed: %v", postErr)
		return
	}
	defer res.Body.Close()

	if res.StatusCode == http.StatusOK || res.StatusCode == http.StatusCreated {
		log.Printf("[Simulator] Successfully auto-seeded Room A109 equipment in BuildSim!")
	} else {
		log.Printf("[Simulator] Auto-seeding returned status %d", res.StatusCode)
	}
}

func readActuatorFloat(client *http.Client, baseURL, actuatorID string, defaultVal float64) float64 {
	resp, err := client.Get(fmt.Sprintf("%s/api/actuators/%s", baseURL, actuatorID))
	if err != nil {
		return defaultVal
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return defaultVal
	}

	var state ActuatorState
	if err := json.NewDecoder(resp.Body).Decode(&state); err != nil {
		return defaultVal
	}

	val, err := strconv.ParseFloat(state.State, 64)
	if err != nil {
		return defaultVal
	}
	return val
}

func readActuatorInt(client *http.Client, baseURL, actuatorID string, defaultVal int) int {
	resp, err := client.Get(fmt.Sprintf("%s/api/actuators/%s", baseURL, actuatorID))
	if err != nil {
		return defaultVal
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return defaultVal
	}

	var state ActuatorState
	if err := json.NewDecoder(resp.Body).Decode(&state); err != nil {
		return defaultVal
	}

	val, err := strconv.Atoi(state.State)
	if err != nil {
		return defaultVal
	}
	return val
}

func readOccupancy(client *http.Client, baseURL, key string) int {
	resp, err := client.Get(fmt.Sprintf("%s/api/occupancy", baseURL))
	if err != nil {
		return 0
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return 0
	}

	var occ OccupancyResponse
	if err := json.NewDecoder(resp.Body).Decode(&occ); err != nil {
		return 0
	}
	if roomData, exists := occ[key]; exists {
		return len(roomData.Persons)
	}
	return 0
}

func writeSensor(client *http.Client, baseURL, sensorID, value string) error {
	payload, _ := json.Marshal(map[string]string{
		"data_type": "text",
		"value":     value,
	})
	req, err := http.NewRequest(http.MethodPut,
		fmt.Sprintf("%s/api/sensors/%s/value", baseURL, sensorID),
		bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	return nil
}

func updateHeatmap(client *http.Client, baseURL string, temp float64) {
	layers := []RoomLayer{
		{
			ID:      "temperature",
			Label:   "Simulated Room Temperature",
			Unit:    "°C",
			Source:  "Physical Thermal Simulator",
			Minimum: 16.0,
			Maximum: 30.0,
			Values: map[string]float64{
				roomKey: temp,
			},
		},
	}
	payload, _ := json.Marshal(layers)
	req, err := http.NewRequest(http.MethodPut, fmt.Sprintf("%s/api/room-layers", baseURL), bytes.NewReader(payload))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
}
