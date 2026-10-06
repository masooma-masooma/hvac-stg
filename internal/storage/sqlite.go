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

	if err := migrate(db); err != nil {
		db.Close()
		return nil, err
	}

	log.Printf("[SQLiteStore] Database initialized successfully at %s (WAL mode active)", dbPath)
	return &SQLiteStore{db: db}, nil
}

// migrate adds the actuator-state columns to databases created by older versions.
func migrate(db *sql.DB) error {
	rows, err := db.Query("PRAGMA table_info(telemetry);")
	if err != nil {
		return fmt.Errorf("failed reading telemetry schema: %w", err)
	}
	existing := map[string]bool{}
	for rows.Next() {
		var cid, notNull, pk int
		var name, colType string
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &colType, &notNull, &dflt, &pk); err != nil {
			rows.Close()
			return err
		}
		existing[name] = true
	}
	rows.Close()

	for _, col := range []struct{ name, ddl string }{
		{"setpoint", "ALTER TABLE telemetry ADD COLUMN setpoint REAL;"},
		{"damper", "ALTER TABLE telemetry ADD COLUMN damper INTEGER;"},
	} {
		if !existing[col.name] {
			if _, err := db.Exec(col.ddl); err != nil {
				return fmt.Errorf("failed adding column %s: %w", col.name, err)
			}
			log.Printf("[SQLiteStore] Migrated telemetry table: added column %s", col.name)
		}
	}
	return nil
}

// timestampLayout has a fixed width so stored timestamps sort lexicographically.
const timestampLayout = "2006-01-02T15:04:05.000Z"

// InsertSnapshot logs an incoming room snapshot and estimated instant power draw
func (s *SQLiteStore) InsertSnapshot(r *models.RoomTelemetrySnapshot) error {
	query := `
	INSERT INTO telemetry (timestamp, level, room, temperature, co2, occupancy, power_w, setpoint, damper)
	VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
	`
	ts := r.Timestamp.UTC().Format(timestampLayout)
	res, err := s.db.Exec(query, ts, r.Level, r.Room, r.Temperature, r.CO2, r.Occupancy, r.PowerW, r.Setpoint, r.Damper)
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
	cutoff := time.Now().UTC().Add(-time.Duration(minutes) * time.Minute).Format(timestampLayout)

	query := `
	SELECT id, timestamp, level, room, temperature, co2, occupancy, power_w, setpoint, damper
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
		var setpoint sql.NullFloat64
		var damper sql.NullInt64
		if err := rows.Scan(&rec.ID, &tsStr, &rec.Level, &rec.Room, &rec.Temperature, &rec.CO2, &rec.Occupancy, &rec.PowerW, &setpoint, &damper); err != nil {
			return nil, err
		}
		if setpoint.Valid {
			v := setpoint.Float64
			rec.Setpoint = &v
		}
		if damper.Valid {
			v := int(damper.Int64)
			rec.Damper = &v
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
		COALESCE(AVG(power_w), 0.0)
	FROM telemetry
	WHERE room = ?;
	`

	// Energy = sum of power x time to the next sample; gaps longer than 5 s
	// (service outages) are not counted.
	energyQuery := `
	SELECT COALESCE(SUM(power_w * MIN(dt, 5.0)), 0.0) FROM (
		SELECT power_w,
		       (julianday(LEAD(timestamp) OVER (ORDER BY timestamp, id)) - julianday(timestamp)) * 86400.0 AS dt
		FROM telemetry WHERE room = ?
	) WHERE dt IS NOT NULL AND dt >= 0;
	`

	var count int
	var avgTemp, minTemp, maxTemp float64
	var avgCO2, minCO2, maxCO2 float64
	var avgPower, energyWs float64

	err := s.db.QueryRow(query, room).Scan(&count, &avgTemp, &minTemp, &maxTemp, &avgCO2, &minCO2, &maxCO2, &avgPower)
	if err != nil {
		return nil, err
	}
	if err := s.db.QueryRow(energyQuery, room).Scan(&energyWs); err != nil {
		return nil, err
	}
	energyKWh := energyWs / 3600000.0

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
