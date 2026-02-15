package handlers

import (
	"agent-executor/internal/agent"
	"agent-executor/internal/policyclient"
	"agent-executor/internal/types"
	"encoding/json"
	"net/http"
	"os"
)

func RunAgentHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req types.AgentRunRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request", http.StatusBadRequest)
		return
	}

	policyURL := getEnv("POLICY_ENGINE_URL", "http://localhost:8080")
	client := policyclient.New(policyURL)

	result, err := agent.Run(req, client)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}

func getEnv(key, fallback string) string {
	if value, ok := os.LookupEnv(key); ok {
		return value
	}
	return fallback
}
