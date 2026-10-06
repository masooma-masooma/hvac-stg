package main

import (
	"encoding/json"
	"fmt"
	"log"
	"math"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"hvac/internal/models"
	"hvac/internal/storage"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

// Power model constants. Must match services/smart-controller/models/dynamics.py.
const (
	ambientTemp    = 12.0
	basePowerW     = 25.0
	fanPowerW      = 45.0   // per damper level
	wattsPerRate   = 8750.0 // W per °C/s of heating (= 350 W per °C of lift)
	maxHeaterW     = 2500.0
	kHVAC          = 0.04
	kEnvelope      = 0.005
	kVentThermal   = 0.008
	thermostatBand = 0.5
	kOccHeat       = 0.01
	actuatorMaxAge = 30 * time.Second // reuse a cached actuator read for at most this long
)

// Sensor plausibility limits; snapshots outside them are rejected, not stored.
const (
	minTempC     = -20.0
	maxTempC     = 60.0
	minCO2ppm    = 300.0
	maxCO2ppm    = 5000.0
	maxOccupancy = 200
)

type actuatorState struct {
	setpoint  float64
	damper    int
	fetchedAt time.Time
}

type IngestorService struct {
	store       *storage.SQLiteStore
	buildsimURL string
	httpClient  *http.Client
	mu          sync.Mutex
	actuators   map[string]actuatorState
	lastStored  map[string]time.Time
	accepted    uint64
	rejected    uint64
	duplicates  uint64
	lastReject  string
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
		store:       store,
		buildsimURL: strings.TrimRight(buildsimURL, "/"),
		httpClient:  &http.Client{Timeout: 2 * time.Second},
		actuators:   map[string]actuatorState{},
		lastStored:  map[string]time.Time{},
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
	// The sensor gateway sends []models.TelemetryReading on the bundle topic
	var readings []models.TelemetryReading
	if err := json.Unmarshal(msg.Payload(), &readings); err != nil {
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

	snapshot, seen := BuildSnapshot(readings)
	if err := ValidateSnapshot(snapshot, seen); err != nil {
		svc.mu.Lock()
		svc.rejected++
		svc.lastReject = err.Error()
		svc.mu.Unlock()
		log.Printf("[Ingestor] Rejected snapshot for %s: %v", snapshot.Room, err)
		return
	}

	// The gateway polls at 2 Hz while the simulator writes at 1 Hz: only store
	// snapshots whose sensor timestamp has advanced. Frozen sensors therefore stop
	// producing rows, which the controller detects as stale telemetry.
	svc.mu.Lock()
	if last, ok := svc.lastStored[snapshot.Room]; ok && !snapshot.Timestamp.After(last) {
		svc.duplicates++
		svc.mu.Unlock()
		return
	}
	svc.lastStored[snapshot.Room] = snapshot.Timestamp
	svc.mu.Unlock()

	if setpoint, damper, ok := svc.currentActuatorState(snapshot.Room); ok {
		snapshot.Setpoint = &setpoint
		snapshot.Damper = &damper
		snapshot.PowerW = CalculatePowerW(setpoint, damper, snapshot.Temperature, snapshot.Occupancy)
	} else {
		log.Printf("[Ingestor] Actuator state for %s unknown; storing snapshot without power estimate", snapshot.Room)
	}

	if err := svc.store.InsertSnapshot(snapshot); err != nil {
		log.Printf("[Ingestor] DB Insert error: %v", err)
		return
	}
	svc.mu.Lock()
	svc.accepted++
	n := svc.accepted
	svc.mu.Unlock()
	if n%10 == 1 {
		log.Printf("[Ingestor] Persisted %s/%s -> Temp=%.1f°C | CO2=%.0f ppm | Occ=%d | Power=%.1f W",
			snapshot.Level, snapshot.Room, snapshot.Temperature, snapshot.CO2, snapshot.Occupancy, snapshot.PowerW)
	}
}

// BuildSnapshot merges a reading bundle into one room snapshot. The snapshot is
// stamped with the oldest sensor timestamp so its age is never understated.
func BuildSnapshot(readings []models.TelemetryReading) (*models.RoomTelemetrySnapshot, map[string]bool) {
	snapshot := &models.RoomTelemetrySnapshot{Room: readings[0].Room, Level: readings[0].Level}
	seen := map[string]bool{}
	for _, r := range readings {
		if !r.Timestamp.IsZero() && (snapshot.Timestamp.IsZero() || r.Timestamp.Before(snapshot.Timestamp)) {
			snapshot.Timestamp = r.Timestamp
		}
		switch r.Type {
		case "temperature":
			snapshot.Temperature = r.Value
		case "co2":
			snapshot.CO2 = r.Value
		case "occupancy":
			snapshot.Occupancy = int(r.Value)
		default:
			continue
		}
		seen[r.Type] = true
	}
	if snapshot.Timestamp.IsZero() {
		snapshot.Timestamp = time.Now().UTC()
	}
	return snapshot, seen
}

// ValidateSnapshot rejects incomplete bundles and physically implausible values.
func ValidateSnapshot(s *models.RoomTelemetrySnapshot, seen map[string]bool) error {
	for _, required := range []string{"temperature", "co2", "occupancy"} {
		if !seen[required] {
			return fmt.Errorf("missing %s reading", required)
		}
	}
	if s.Temperature < minTempC || s.Temperature > maxTempC {
		return fmt.Errorf("temperature %.1f°C outside [%.0f, %.0f]", s.Temperature, minTempC, maxTempC)
	}
	if s.CO2 < minCO2ppm || s.CO2 > maxCO2ppm {
		return fmt.Errorf("CO2 %.0f ppm outside [%.0f, %.0f]", s.CO2, minCO2ppm, maxCO2ppm)
	}
	if s.Occupancy < 0 || s.Occupancy > maxOccupancy {
		return fmt.Errorf("occupancy %d outside [0, %d]", s.Occupancy, maxOccupancy)
	}
	return nil
}

// currentActuatorState reads the live setpoint and damper from BuildSim, falling
// back to the last successful read if it is recent enough.
func (svc *IngestorService) currentActuatorState(room string) (float64, int, bool) {
	setpoint, errSp := svc.readActuator(room + "-setpoint")
	damper, errD := svc.readActuator(room + "-damper")

	svc.mu.Lock()
	defer svc.mu.Unlock()
	if errSp == nil && errD == nil {
		st := actuatorState{setpoint: setpoint, damper: int(damper), fetchedAt: time.Now()}
		svc.actuators[room] = st
		return st.setpoint, st.damper, true
	}
	if st, ok := svc.actuators[room]; ok && time.Since(st.fetchedAt) < actuatorMaxAge {
		return st.setpoint, st.damper, true
	}
	return 0, 0, false
}

func (svc *IngestorService) readActuator(id string) (float64, error) {
	resp, err := svc.httpClient.Get(fmt.Sprintf("%s/api/actuators/%s", svc.buildsimURL, id))
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 0, fmt.Errorf("BuildSim returned %s for %s", resp.Status, id)
	}
	var body struct {
		State string `json:"state"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return 0, err
	}
	return strconv.ParseFloat(body.State, 64)
}

// CalculatePowerW returns the electrical power draw (W) of Room A109's HVAC:
// 25 W standby + 45 W per damper level + radiator heat. The radiator thermostat
// delivers K_HVAC*(setpoint - T) plus whatever offsets envelope and ventilation
// losses minus occupant gains (fading out above the setpoint), priced at 350 W
// per °C of lift, capped at 2.5 kW.
func CalculatePowerW(setpoint float64, damper int, temperature float64, occupancy int) float64 {
	loss := (kEnvelope + kVentThermal*float64(damper)) * (temperature - ambientTemp)
	gain := kOccHeat * float64(occupancy)
	compensation := math.Max(0, math.Min(1, 1-(temperature-setpoint)/thermostatBand))
	demand := kHVAC*(setpoint-temperature) + compensation*(loss-gain)
	heaterW := math.Max(0, math.Min(demand*wattsPerRate, maxHeaterW))
	return basePowerW + fanPowerW*float64(damper) + heaterW
}

func (svc *IngestorService) counters() map[string]interface{} {
	svc.mu.Lock()
	defer svc.mu.Unlock()
	return map[string]interface{}{
		"accepted_snapshots":  svc.accepted,
		"rejected_snapshots":  svc.rejected,
		"duplicate_snapshots": svc.duplicates,
		"last_reject_reason":  svc.lastReject,
	}
}

func (svc *IngestorService) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	resp := map[string]interface{}{"status": "UP", "service": "telemetry-ingestor"}
	for k, v := range svc.counters() {
		resp[k] = v
	}
	json.NewEncoder(w).Encode(resp)
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

	stats["data_quality"] = svc.counters()
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
