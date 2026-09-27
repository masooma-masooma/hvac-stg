package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
	"time"

	"hvac/internal/models"
	"hvac/internal/storage"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

type IngestorService struct {
	store        *storage.SQLiteStore
	buildsimURL  string
	mu           sync.RWMutex
	lastSetpoint float64
	lastDamper   int
}

func main() {
	mqttBroker := os.Getenv("MQTT_BROKER")
	if mqttBroker == "" {
		mqttBroker = "tcp://127.0.0.1:1883"
	}
	dbPath := os.Getenv("DB_PATH")
	if dbPath == "" {
		dbPath = "./data/db/hvac.db"
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8081"
	}
	buildsimURL := os.Getenv("BUILDSIM_URL")
	if buildsimURL == "" {
		buildsimURL = "http://127.0.0.1:9090"
	}

	log.Printf("[Ingestor] Initializing Telemetry Ingestor & Time-Series Pipeline...")
	log.Printf("[Ingestor] Database: %s | MQTT: %s | HTTP Port: :%s", dbPath, mqttBroker, port)

	// 1. Initialize SQLite Store
	store, err := storage.NewSQLiteStore(dbPath)
	if err != nil {
		log.Fatalf("[Ingestor] Failed to initialize SQLite storage: %v", err)
	}
	defer store.Close()

	svc := &IngestorService{
		store:        store,
		buildsimURL:  buildsimURL,
		lastSetpoint: 21.0,
		lastDamper:   1,
	}

	// 2. Connect to MQTT Broker
	opts := mqtt.NewClientOptions()
	opts.AddBroker(mqttBroker)
	opts.SetClientID("hvac-telemetry-ingestor")
	opts.SetAutoReconnect(true)
	opts.SetConnectRetry(true)
	opts.SetConnectRetryInterval(2 * time.Second)

	opts.OnConnect = func(c mqtt.Client) {
		log.Println("[Ingestor] Connected to MQTT Broker. Subscribing to telemetry topics...")
		topic := "building/+/+/telemetry"
		if token := c.Subscribe(topic, 0, svc.handleTelemetryMessage); token.Wait() && token.Error() != nil {
			log.Printf("[Ingestor] Failed to subscribe to %s: %v", topic, token.Error())
		} else {
			log.Printf("[Ingestor] Subscribed successfully to: %s", topic)
		}
	}

	mqttClient := mqtt.NewClient(opts)
	if token := mqttClient.Connect(); token.Wait() && token.Error() != nil {
		log.Printf("[Ingestor] Initial MQTT connection failed (%v). Retrying in background...", token.Error())
	}

	// 3. HTTP Server setup (Query & Observability API)
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", svc.handleHealth)
	mux.HandleFunc("/api/history", svc.handleHistory)
	mux.HandleFunc("/api/stats", svc.handleStats)
	mux.HandleFunc("/api/export/jsonl", svc.handleExportJSONL)

	server := &http.Server{
		Addr:    ":" + port,
		Handler: mux,
	}

	go func() {
		log.Printf("[Ingestor] HTTP Query API listening on :%s", port)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("[Ingestor] HTTP server error: %v", err)
		}
	}()

	// 4. Graceful shutdown
	stopChan := make(chan os.Signal, 1)
	signal.Notify(stopChan, os.Interrupt, syscall.SIGTERM)
	<-stopChan

	log.Println("[Ingestor] Shutting down gracefully...")
	mqttClient.Disconnect(250)
	server.Close()
	log.Println("[Ingestor] Service stopped.")
}

func (svc *IngestorService) handleTelemetryMessage(client mqtt.Client, msg mqtt.Message) {
	// The sensor gateway sends []models.TelemetryReading on bundle topic
	var readings []models.TelemetryReading
	if err := json.Unmarshal(msg.Payload(), &readings); err != nil {
		// Single reading fallback
		var single models.TelemetryReading
		if err2 := json.Unmarshal(msg.Payload(), &single); err2 != nil {
			log.Printf("[Ingestor] Invalid JSON on topic %s: %v", msg.Topic(), err)
			return
		}
		readings = []models.TelemetryReading{single}
	}

	if len(readings) == 0 {
		return
	}

	snapshot := models.RoomTelemetrySnapshot{
		Timestamp: time.Now().UTC(),
		Room:      readings[0].Room,
		Level:     readings[0].Level,
	}

	for _, r := range readings {
		if !r.Timestamp.IsZero() {
			snapshot.Timestamp = r.Timestamp
		}
		switch r.Type {
		case "temperature":
			snapshot.Temperature = r.Value
		case "co2":
			snapshot.CO2 = r.Value
		case "occupancy":
			snapshot.Occupancy = int(r.Value)
		}
	}

	snapshot.PowerW = svc.calculatePowerW(&snapshot)

	if err := svc.store.InsertSnapshot(&snapshot); err != nil {
		log.Printf("[Ingestor] DB Insert error: %v", err)
	} else {
		log.Printf("[Ingestor] Persisted %s/%s -> Temp=%.1f°C | CO2=%.0f ppm | Occ=%d | Power=%.1f W",
			snapshot.Level, snapshot.Room, snapshot.Temperature, snapshot.CO2, snapshot.Occupancy, snapshot.PowerW)
	}
}

// calculatePowerW models electrical power draw in Watts
// Base standby (25W) + Fan power (45W per damper level) + Proportional heating (350W per °C below target)
func (svc *IngestorService) calculatePowerW(s *models.RoomTelemetrySnapshot) float64 {
	basePower := 25.0
	fanPower := float64(svc.lastDamper) * 45.0

	heatingPower := 0.0
	tempDiff := svc.lastSetpoint - s.Temperature
	if tempDiff > 0 {
		heatingPower = tempDiff * 350.0 // 350 W per degree of heating lift
		if heatingPower > 2500.0 {
			heatingPower = 2500.0 // Max radiator power 2.5 kW
		}
	}

	return basePower + fanPower + heatingPower
}

func (svc *IngestorService) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "UP", "service": "telemetry-ingestor"})
}

func (svc *IngestorService) handleHistory(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	room := r.URL.Query().Get("room")
	if room == "" {
		room = "A109"
	}
	minStr := r.URL.Query().Get("minutes")
	minutes := 30
	if m, err := strconv.Atoi(minStr); err == nil && m > 0 {
		minutes = m
	}

	records, err := svc.store.GetRecentTelemetry(room, minutes)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"%s"}`, err.Error()), http.StatusInternalServerError)
		return
	}

	if records == nil {
		records = []models.RoomTelemetrySnapshot{}
	}

	json.NewEncoder(w).Encode(records)
}

func (svc *IngestorService) handleStats(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	room := r.URL.Query().Get("room")
	if room == "" {
		room = "A109"
	}

	stats, err := svc.store.GetRoomStats(room)
	if err != nil {
		http.Error(w, fmt.Sprintf(`{"error":"%s"}`, err.Error()), http.StatusInternalServerError)
		return
	}

	json.NewEncoder(w).Encode(stats)
}

// handleExportJSONL implements Bronze tier raw export recommended in Course Notes 3
func (svc *IngestorService) handleExportJSONL(w http.ResponseWriter, r *http.Request) {
	room := r.URL.Query().Get("room")
	if room == "" {
		room = "A109"
	}
	records, err := svc.store.GetRecentTelemetry(room, 1440) // last 24h
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/x-ndjson")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=%s_telemetry_bronze.jsonl", room))
	encoder := json.NewEncoder(w)
	for _, rec := range records {
		_ = encoder.Encode(rec)
	}
}
