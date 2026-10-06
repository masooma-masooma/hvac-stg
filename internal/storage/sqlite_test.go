package storage

import (
	"database/sql"
	"math"
	"path/filepath"
	"testing"
	"time"

	"hvac/internal/models"
)

func TestMigrationAddsActuatorColumnsToLegacyDatabase(t *testing.T) {
	path := filepath.Join(t.TempDir(), "legacy.db")
	legacy, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = legacy.Exec(`CREATE TABLE telemetry (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME NOT NULL,
		level TEXT NOT NULL, room TEXT NOT NULL, temperature REAL NOT NULL, co2 REAL NOT NULL,
		occupancy INTEGER NOT NULL, power_w REAL NOT NULL);
		INSERT INTO telemetry (timestamp, level, room, temperature, co2, occupancy, power_w)
		VALUES ('2020-01-01T00:00:00Z', 'level0', 'A109', 21, 500, 0, 100);`)
	legacy.Close()
	if err != nil {
		t.Fatal(err)
	}

	store, err := NewSQLiteStore(path)
	if err != nil {
		t.Fatalf("migration failed: %v", err)
	}
	defer store.Close()

	sp, d := 21.5, 2
	if err := store.InsertSnapshot(&models.RoomTelemetrySnapshot{Timestamp: time.Now().UTC(), Level: "level0", Room: "A109",
		Temperature: 21, CO2: 600, Occupancy: 3, PowerW: 200, Setpoint: &sp, Damper: &d}); err != nil {
		t.Fatalf("insert after migration failed: %v", err)
	}
	recs, err := store.GetRecentTelemetry("A109", 5)
	if err != nil || len(recs) != 1 {
		t.Fatalf("expected 1 recent record, got %d (%v)", len(recs), err)
	}
	if recs[0].Setpoint == nil || *recs[0].Setpoint != 21.5 || recs[0].Damper == nil || *recs[0].Damper != 2 {
		t.Fatalf("actuator state not round-tripped: %+v", recs[0])
	}
}

func TestRoomStatsIntegratesEnergyOverTimestamps(t *testing.T) {
	store, err := NewSQLiteStore(filepath.Join(t.TempDir(), "energy.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	start := time.Date(2026, 10, 6, 10, 0, 0, 0, time.UTC)
	// 3600 W for 1 s, 1 s, then a 60 s outage (capped at 5 s), then a final row.
	offsets := []time.Duration{0, time.Second, 2 * time.Second, 62 * time.Second}
	for _, off := range offsets {
		if err := store.InsertSnapshot(&models.RoomTelemetrySnapshot{Timestamp: start.Add(off), Level: "level0", Room: "A109",
			Temperature: 21, CO2: 500, PowerW: 3600}); err != nil {
			t.Fatal(err)
		}
	}
	stats, err := store.GetRoomStats("A109")
	if err != nil {
		t.Fatal(err)
	}
	got := stats["power"].(map[string]float64)["total_energy_kwh"]
	want := 3600.0 * (1 + 1 + 5) / 3600000.0
	if math.Abs(got-want) > 1e-7 { // julianday() arithmetic is not exact
		t.Fatalf("expected %.6f kWh, got %.6f kWh", want, got)
	}
}
