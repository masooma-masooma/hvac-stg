package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"hvac/internal/models"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

const (
	defaultBaseURL = "http://127.0.0.1:9090"
	defaultMQTT    = "tcp://127.0.0.1:1883"
	roomName       = "A109"
	levelName      = "level0"
)

type BuildSimSensor struct {
	ID        string    `json:"id"`
	Type      string    `json:"type"`
	Value     string    `json:"value"`
	Unit      string    `json:"unit"`
	Timestamp time.Time `json:"timestamp"` // when the physical simulator last wrote the value
}

// pollInterval is 500 ms (2 Hz). The physical simulator writes at 1 Hz, so the
// ingestor drops readings whose sensor timestamp has not advanced.
const pollInterval = 500 * time.Millisecond

func main() {
	baseURL := strings.TrimRight(os.Getenv("BUILDSIM_URL"), "/")
	if baseURL == "" {
		baseURL = defaultBaseURL
	}

	brokerURL := os.Getenv("MQTT_BROKER")
	if brokerURL == "" {
		brokerURL = defaultMQTT
	}

	log.Printf("[SensorGateway] Starting IoT Sensor Gateway for Room %s...", roomName)
	log.Printf("[SensorGateway] BuildSim: %s | MQTT Broker: %s", baseURL, brokerURL)

	// Initialize MQTT Client
	opts := mqtt.NewClientOptions().
		AddBroker(brokerURL).
		SetClientID(fmt.Sprintf("sensor-gateway-%s", roomName)).
		SetCleanSession(true).
		SetAutoReconnect(true).
		SetConnectRetry(true).
		SetConnectRetryInterval(2 * time.Second)

	client := mqtt.NewClient(opts)
	for {
		token := client.Connect()
		if token.Wait() && token.Error() == nil {
			log.Printf("[SensorGateway] Connected to MQTT Broker successfully!")
			break
		}
		log.Printf("[SensorGateway] Waiting for MQTT broker at %s: %v", brokerURL, token.Error())
		time.Sleep(2 * time.Second)
	}

	httpClient := &http.Client{Timeout: 5 * time.Second}
	ticker := time.NewTicker(pollInterval)
	defer ticker.Stop()

	sensors := []string{"A109-temp", "A109-co2", "A109-occ"}

	published := 0
	for range ticker.C {
		var readings []models.TelemetryReading

		for _, sensorID := range sensors {
			sensor, err := fetchSensor(httpClient, baseURL, sensorID)
			if err != nil {
				continue
			}

			val, err := strconv.ParseFloat(sensor.Value, 64)
			if err != nil {
				continue
			}

			// Keep the sensor's own timestamp so stale values stay detectable downstream.
			ts := sensor.Timestamp.UTC()
			if ts.IsZero() {
				ts = time.Now().UTC()
			}
			reading := models.TelemetryReading{
				Room:      roomName,
				Level:     levelName,
				DeviceID:  sensor.ID,
				Type:      sensor.Type,
				Value:     val,
				Unit:      sensor.Unit,
				Timestamp: ts,
			}
			readings = append(readings, reading)

			// Publish to sensor-specific topic
			topic := fmt.Sprintf("building/%s/%s/sensor/%s", levelName, roomName, sensor.Type)
			payload, _ := json.Marshal(reading)
			client.Publish(topic, 0, false, payload)
		}

		// Publish aggregate bundle to telemetry topic
		if len(readings) > 0 {
			bundleTopic := fmt.Sprintf("building/%s/%s/telemetry", levelName, roomName)
			bundlePayload, _ := json.Marshal(readings)
			client.Publish(bundleTopic, 0, false, bundlePayload)

			var tempVal, co2Val, occVal float64
			for _, r := range readings {
				switch r.Type {
				case "temperature":
					tempVal = r.Value
				case "co2":
					co2Val = r.Value
				case "occupancy":
					occVal = r.Value
				}
			}
			published++
			if published%10 != 1 {
				continue
			}
			log.Printf("[SensorGateway] Telemetry published -> Room %s: Temp=%.1f°C | CO2=%.0f ppm | Occ=%.0f",
				roomName, tempVal, co2Val, occVal)
		}
	}
}

func fetchSensor(client *http.Client, baseURL, sensorID string) (*BuildSimSensor, error) {
	resp, err := client.Get(fmt.Sprintf("%s/api/sensors/%s", baseURL, sensorID))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP error: %s", resp.Status)
	}

	var s BuildSimSensor
	if err := json.NewDecoder(resp.Body).Decode(&s); err != nil {
		return nil, err
	}
	return &s, nil
}
