package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

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
		baseURL = "http://127.0.0.1:9090"
	}

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
				{
					ID:       "A109-temp",
					Name:     "Indoor Temperature",
					Type:     "temperature",
					DataType: "text",
					Unit:     "°C",
					Value:    "20.5",
				},
				{
					ID:       "A109-co2",
					Name:     "Indoor CO2 Concentration",
					Type:     "co2",
					DataType: "text",
					Unit:     "ppm",
					Value:    "450",
				},
				{
					ID:       "A109-occ",
					Name:     "Room Occupancy",
					Type:     "occupancy",
					DataType: "text",
					Unit:     "persons",
					Value:    "0",
				},
			},
			Actuators: []ActuatorDefinition{
				{
					ID:    "A109-setpoint",
					Name:  "Heating Setpoint",
					Type:  "setpoint",
					State: "21.0",
				},
				{
					ID:    "A109-damper",
					Name:  "Ventilation Damper Level",
					Type:  "fan_speed",
					State: "1",
				},
			},
		},
	}

	body, err := json.Marshal(equipment)
	if err != nil {
		log.Fatalf("Failed to serialize equipment: %v", err)
	}

	client := &http.Client{Timeout: 10 * time.Second}
	url := baseURL + "/api/equipment/bulk"

	fmt.Printf("Seeding equipment to BuildSim at %s...\n", url)
	resp, err := client.Post(url, "application/json", bytes.NewReader(body))
	if err != nil {
		log.Fatalf("Could not reach BuildSim: %v (is BuildSim running?)", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusCreated {
		log.Fatalf("BuildSim rejected seeding with status %s: %s", resp.Status, string(respBody))
	}

	fmt.Println(" Successfully registered equipment in Room A109:")
	fmt.Println("  - Equipment : hvac-A109 (Level 0, Room A109)")
	fmt.Println("  - Sensors   : A109-temp (20.5 °C), A109-co2 (450 ppm), A109-occ (0 persons)")
	fmt.Println("  - Actuators : A109-setpoint (21.0 °C), A109-damper (level 1)")
}
