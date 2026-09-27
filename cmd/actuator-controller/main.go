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
	"sync"
	"time"

	"hvac/internal/models"
)

const (
	defaultBaseURL = "http://127.0.0.1:9090"
	listenPort     = ":8080"
	minSetpoint    = 16.0
	maxSetpoint    = 28.0
)

type Controller struct {
	baseURL    string
	httpClient *http.Client
	mu         sync.Mutex
	lastChange time.Time
}

func main() {
	baseURL := strings.TrimRight(os.Getenv("BUILDSIM_URL"), "/")
	if baseURL == "" {
		baseURL = defaultBaseURL
	}

	ctrl := &Controller{
		baseURL:    baseURL,
		httpClient: &http.Client{Timeout: 5 * time.Second},
	}

	http.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	})

	http.HandleFunc("/commands", ctrl.handleCommand)

	log.Printf("[ActuatorService] Listening for actuation commands on %s...", listenPort)
	log.Printf("[ActuatorService] Target BuildSim URL: %s", baseURL)
	if err := http.ListenAndServe(listenPort, nil); err != nil {
		log.Fatalf("Server failed: %v", err)
	}
}

func (c *Controller) handleCommand(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed. Use POST.", http.StatusMethodNotAllowed)
		return
	}

	var cmd models.ActuatorCommand
	if err := json.NewDecoder(r.Body).Decode(&cmd); err != nil {
		http.Error(w, fmt.Sprintf("Invalid JSON: %v", err), http.StatusBadRequest)
		return
	}

	c.mu.Lock()
	defer c.mu.Unlock()

	var appliedActions []string

	// 1. Process Setpoint Command
	if cmd.Setpoint != nil {
		target := *cmd.Setpoint

		// Simplex Safety Guardrail Clamp: 16.0 <= T <= 28.0
		if target < minSetpoint {
			log.Printf("[ActuatorService] Warning: Requested setpoint %.1f°C below safe limit. Clamping to %.1f°C.", target, minSetpoint)
			target = minSetpoint
		} else if target > maxSetpoint {
			log.Printf("[ActuatorService] Warning: Requested setpoint %.1f°C above safe limit. Clamping to %.1f°C.", target, maxSetpoint)
			target = maxSetpoint
		}

		err := c.applyActuatorState("A109-setpoint", fmt.Sprintf("%.1f", target))
		if err != nil {
			http.Error(w, fmt.Sprintf("BuildSim error: %v", err), http.StatusBadGateway)
			return
		}
		appliedActions = append(appliedActions, fmt.Sprintf("Setpoint=%.1f°C", target))
	}

	// 2. Process Ventilation Damper Command
	if cmd.Damper != nil {
		damper := *cmd.Damper
		if damper < 0 {
			damper = 0
		} else if damper > 3 {
			damper = 3
		}

		err := c.applyActuatorState("A109-damper", fmt.Sprintf("%d", damper))
		if err != nil {
			http.Error(w, fmt.Sprintf("BuildSim error: %v", err), http.StatusBadGateway)
			return
		}
		appliedActions = append(appliedActions, fmt.Sprintf("Damper=%d", damper))
	}

	c.lastChange = time.Now()

	msg := fmt.Sprintf("Applied actions for Room %s: %s (Reason: %s)", cmd.Room, strings.Join(appliedActions, ", "), cmd.Reason)
	log.Printf("[ActuatorService] %s", msg)

	resp := models.ActuatorResponse{
		Success:   true,
		Room:      cmd.Room,
		Message:   msg,
		Timestamp: time.Now().UTC(),
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func (c *Controller) applyActuatorState(actuatorID, state string) error {
	payload, _ := json.Marshal(map[string]string{
		"state": state,
	})

	url := fmt.Sprintf("%s/api/actuators/%s/state", c.baseURL, actuatorID)
	req, err := http.NewRequest(http.MethodPut, url, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("BuildSim returned %s: %s", resp.Status, string(body))
	}
	return nil
}
