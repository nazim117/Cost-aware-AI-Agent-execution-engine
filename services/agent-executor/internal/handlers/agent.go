package handlers

import (
	"encoding/json"
	"net/http"
	"os"

	"agent-executor/internal/agent"
	"agent-executor/internal/policyclient"
	"agent-executor/internal/types"
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

	policyURL := os.Getenv("POLICY_ENGINE_URL")
	if policyURL == "" {
		policyURL = "http://localhost:8080"
	}

	client := policyclient.New(policyURL)

	result, err := agent.RunAgent(req, client)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}
