package handlers

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"

	"agent-executor/internal/agent"
	"agent-executor/internal/gatewayclient"
	"agent-executor/internal/metrics"
	"agent-executor/internal/policyclient"
	"agent-executor/internal/types"
)

func RunAgentHandler(m *metrics.Metrics) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var req types.AgentRunRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid request", http.StatusBadRequest)
			return
		}

		policyURL := os.Getenv("POLICY_ENGINE_URL")
		if policyURL == "" {
			policyURL = "http://localhost:8080"
			log.Printf("POLICY_ENGINE_URL not set, defaulting to %s", policyURL)
		}

		client := policyclient.New(policyURL)

		result, err := agent.RunAgent(req, client, m)
		if err != nil {
			var piiErr *gatewayclient.PIIBlockedError
			if errors.As(err, &piiErr) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusUnprocessableEntity)
				json.NewEncoder(w).Encode(map[string]interface{}{
					"error": map[string]interface{}{
						"type":      "pii_violation",
						"message":   "Request blocked: PII detected in goal",
						"pii_types": piiErr.PIITypes,
					},
				})
				return
			}
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(result)
	}
}
