package storage

import (
	"database/sql"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"time"

	"hvac/internal/models"

	_ "modernc.org/sqlite"
)

type SQLiteStore struct {
	db *sql.DB
}

// NewSQLiteStore initializes SQLite with Write-Ahead Logging (WAL) and creates the schema
func NewSQLiteStore(dbPath string) (*SQLiteStore, error) {
	dir := filepath.Dir(dbPath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, fmt.Errorf("failed to create db directory %s: %w", dir, err)
	}

	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, fmt.Errorf("failed to open sqlite database at %s: %w", dbPath, err)
	}

	// SQLite single-writer safety
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)

	pragmas := []string{
		"PRAGMA journal_mode = WAL;",
		"PRAGMA synchronous = NORMAL;",
		"PRAGMA busy_timeout = 5000;",
		"PRAGMA foreign_keys = ON;",
	}
	for _, p := range pragmas {
		if _, err := db.Exec(p); err != nil {
			return nil, fmt.Errorf("failed executing %s: %w", p, err)
		}
	}

	schema := `
	CREATE TABLE IF NOT EXISTS telemetry (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		timestamp DATETIME NOT NULL,
		level TEXT NOT NULL,
		room TEXT NOT NULL,
		temperature REAL NOT NULL,
		co2 REAL NOT NULL,
		occupancy INTEGER NOT NULL,
		power_w REAL NOT NULL
	);

	CREATE TABLE IF NOT EXISTS actuation_history (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		timestamp DATETIME NOT NULL,
		room TEXT NOT NULL,
		setpoint REAL,
		damper INTEGER,
		reason TEXT
	);

	CREATE INDEX IF NOT EXISTS idx_telemetry_room_ts ON telemetry(room, timestamp DESC);
	CREATE INDEX IF NOT EXISTS idx_actuation_room_ts ON actuation_history(room, timestamp DESC);
	`

	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("failed executing schema: %w", err)
	}

	log.Printf("[SQLiteStore] Database initialized successfully at %s (WAL mode active)", dbPath)
	return &SQLiteStore{db: db}, nil
}

// InsertSnapshot logs an incoming room snapshot and estimated instant power draw
func (s *SQLiteStore) InsertSnapshot(r *models.RoomTelemetrySnapshot) error {
	query := `
	INSERT INTO telemetry (timestamp, level, room, temperature, co2, occupancy, power_w)
	VALUES (?, ?, ?, ?, ?, ?, ?);
	`
	ts := r.Timestamp.UTC().Format(time.RFC3339)
	res, err := s.db.Exec(query, ts, r.Level, r.Room, r.Temperature, r.CO2, r.Occupancy, r.PowerW)
	if err != nil {
		return err
	}
	id, _ := res.LastInsertId()
	r.ID = id
	return nil
}

// InsertActuation logs an actuator state modification event
func (s *SQLiteStore) InsertActuation(cmd *models.ActuatorCommand) error {
	query := `
	INSERT INTO actuation_history (timestamp, room, setpoint, damper, reason)
	VALUES (?, ?, ?, ?, ?);
	`
	now := time.Now().UTC().Format(time.RFC3339)
	_, err := s.db.Exec(query, now, cmd.Room, cmd.Setpoint, cmd.Damper, cmd.Reason)
	return err
}

// GetRecentTelemetry returns the latest telemetry records for a given room within the past N minutes
func (s *SQLiteStore) GetRecentTelemetry(room string, minutes int) ([]models.RoomTelemetrySnapshot, error) {
	if minutes <= 0 {
		minutes = 30
	}
	cutoff := time.Now().UTC().Add(-time.Duration(minutes) * time.Minute).Format(time.RFC3339)

	query := `
	SELECT id, timestamp, level, room, temperature, co2, occupancy, power_w
	FROM telemetry
	WHERE room = ? AND timestamp >= ?
	ORDER BY timestamp ASC;
	`

	rows, err := s.db.Query(query, room, cutoff)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var records []models.RoomTelemetrySnapshot
	for rows.Next() {
		var rec models.RoomTelemetrySnapshot
		var tsStr string
		if err := rows.Scan(&rec.ID, &tsStr, &rec.Level, &rec.Room, &rec.Temperature, &rec.CO2, &rec.Occupancy, &rec.PowerW); err != nil {
			return nil, err
		}
		rec.Timestamp, _ = time.Parse(time.RFC3339, tsStr)
		records = append(records, rec)
	}

	return records, rows.Err()
}

// GetRoomStats calculates key metrics: count, min/max/avg temp, min/max/avg co2, and cumulative energy (kWh)
func (s *SQLiteStore) GetRoomStats(room string) (map[string]interface{}, error) {
	query := `
	SELECT 
		COUNT(*),
		COALESCE(AVG(temperature), 0.0),
		COALESCE(MIN(temperature), 0.0),
		COALESCE(MAX(temperature), 0.0),
		COALESCE(AVG(co2), 0.0),
		COALESCE(MIN(co2), 0.0),
		COALESCE(MAX(co2), 0.0),
		COALESCE(AVG(power_w), 0.0),
		COALESCE(SUM(power_w), 0.0)
	FROM telemetry
	WHERE room = ?;
	`

	var count int
	var avgTemp, minTemp, maxTemp float64
	var avgCO2, minCO2, maxCO2 float64
	var avgPower, sumPower float64

	err := s.db.QueryRow(query, room).Scan(&count, &avgTemp, &minTemp, &maxTemp, &avgCO2, &minCO2, &maxCO2, &avgPower, &sumPower)
	if err != nil {
		return nil, err
	}

	// Assuming 2-second sampling interval: each row represents 2 seconds of power.
	// Energy (kWh) = (Sum(power_w) * 2 seconds) / (3600 seconds/hr * 1000 W/kW)
	energyKWh := (sumPower * 2.0) / 3600000.0

	return map[string]interface{}{
		"room":             room,
		"samples_recorded": count,
		"temperature": map[string]float64{
			"avg": avgTemp,
			"min": minTemp,
			"max": maxTemp,
		},
		"co2": map[string]float64{
			"avg": avgCO2,
			"min": minCO2,
			"max": maxCO2,
		},
		"power": map[string]float64{
			"avg_watts":        avgPower,
			"total_energy_kwh": energyKWh,
		},
	}, nil
}

// Close closes the underlying database connection
func (s *SQLiteStore) Close() error {
	return s.db.Close()
}
