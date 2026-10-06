package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"hvac/internal/models"
)

// mockBuildSim creates a local HTTP test server simulating BuildSim actuator endpoints
func mockBuildSim() *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/api/actuators/") && r.Method == http.MethodPut {
			w.WriteHeader(http.StatusOK)
			w.Write([]byte(`{"status":"updated"}`))
			return
		}
		http.NotFound(w, r)
	}))
}

func TestActuatorController_ClampValidation(t *testing.T) {
	ts := mockBuildSim()
	defer ts.Close()

	ctrl := &Controller{
		baseURL:    ts.URL,
		httpClient: ts.Client(),
	}

	tests := []struct {
		name                string
		inputSetpoint       *float64
		inputDamper         *int
		expectedSetpointMsg string
		expectedDamperMsg   string
		expectedStatus      int
	}{
		{
			name:                "Normal valid command within safety bounds",
			inputSetpoint:       floatPtr(22.0),
			inputDamper:         intPtr(2),
			expectedSetpointMsg: "Setpoint=22.0°C",
			expectedDamperMsg:   "Damper=2",
			expectedStatus:      http.StatusOK,
		},
		{
			name:                "Under-temperature clamp (10.0°C -> 16.0°C)",
			inputSetpoint:       floatPtr(10.0),
			inputDamper:         intPtr(1),
			expectedSetpointMsg: "Setpoint=16.0°C",
			expectedDamperMsg:   "Damper=1",
			expectedStatus:      http.StatusOK,
		},
		{
			name:                "Over-temperature clamp (45.0°C -> 28.0°C)",
			inputSetpoint:       floatPtr(45.0),
			inputDamper:         intPtr(1),
			expectedSetpointMsg: "Setpoint=28.0°C",
			expectedDamperMsg:   "Damper=1",
			expectedStatus:      http.StatusOK,
		},
		{
			name:                "Negative damper clamp (-2 -> 0)",
			inputSetpoint:       floatPtr(21.0),
			inputDamper:         intPtr(-2),
			expectedSetpointMsg: "Setpoint=21.0°C",
			expectedDamperMsg:   "Damper=0",
			expectedStatus:      http.StatusOK,
		},
		{
			name:                "Excessive damper clamp (5 -> 3)",
			inputSetpoint:       floatPtr(21.0),
			inputDamper:         intPtr(5),
			expectedSetpointMsg: "Setpoint=21.0°C",
			expectedDamperMsg:   "Damper=3",
			expectedStatus:      http.StatusOK,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cmd := models.ActuatorCommand{
				Room:     "A109",
				Setpoint: tt.inputSetpoint,
				Damper:   tt.inputDamper,
				Reason:   "Unit Test Clamp Verification",
			}
			body, _ := json.Marshal(cmd)

			req := httptest.NewRequest(http.MethodPost, "/commands", bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()

			ctrl.handleCommand(w, req)

			if w.Code != tt.expectedStatus {
				t.Fatalf("Expected HTTP status %d, got %d", tt.expectedStatus, w.Code)
			}

			var resp models.ActuatorResponse
			if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
				t.Fatalf("Failed to decode response: %v", err)
			}

			if !resp.Success {
				t.Errorf("Expected success=true, got false")
			}
			if !strings.Contains(resp.Message, tt.expectedSetpointMsg) {
				t.Errorf("Expected message to contain '%s', got '%s'", tt.expectedSetpointMsg, resp.Message)
			}
			if !strings.Contains(resp.Message, tt.expectedDamperMsg) {
				t.Errorf("Expected message to contain '%s', got '%s'", tt.expectedDamperMsg, resp.Message)
			}
		})
	}
}

func TestActuatorController_HTTPMethodValidation(t *testing.T) {
	ctrl := &Controller{
		baseURL:    "http://127.0.0.1:9090",
		httpClient: &http.Client{Timeout: 1 * time.Second},
	}

	req := httptest.NewRequest(http.MethodGet, "/commands", nil)
	w := httptest.NewRecorder()
	ctrl.handleCommand(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("Expected HTTP 405 Method Not Allowed for GET, got %d", w.Code)
	}
}

func TestActuatorController_MalformedJSON(t *testing.T) {
	ctrl := &Controller{
		baseURL:    "http://127.0.0.1:9090",
		httpClient: &http.Client{Timeout: 1 * time.Second},
	}

	req := httptest.NewRequest(http.MethodPost, "/commands", strings.NewReader("{invalid-json"))
	w := httptest.NewRecorder()
	ctrl.handleCommand(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("Expected HTTP 400 Bad Request for malformed JSON, got %d", w.Code)
	}
}

func TestActuatorController_UnknownRoomRejected(t *testing.T) {
	ts := mockBuildSim()
	defer ts.Close()
	ctrl := &Controller{baseURL: ts.URL, httpClient: ts.Client()}

	body, _ := json.Marshal(models.ActuatorCommand{Room: "B201", Setpoint: floatPtr(21.0)})
	req := httptest.NewRequest(http.MethodPost, "/commands", bytes.NewReader(body))
	w := httptest.NewRecorder()
	ctrl.handleCommand(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("Expected HTTP 400 for unknown room, got %d", w.Code)
	}
}

func TestActuatorController_CORSPreflight(t *testing.T) {
	ctrl := &Controller{baseURL: "http://127.0.0.1:9090", httpClient: &http.Client{Timeout: time.Second}}
	req := httptest.NewRequest(http.MethodOptions, "/commands", nil)
	w := httptest.NewRecorder()
	withCORS(ctrl.handleCommand)(w, req)

	if w.Code != http.StatusNoContent || w.Header().Get("Access-Control-Allow-Origin") != "*" {
		t.Errorf("Expected 204 with CORS headers, got %d %v", w.Code, w.Header())
	}
}

func floatPtr(f float64) *float64 { return &f }
func intPtr(i int) *int           { return &i }
