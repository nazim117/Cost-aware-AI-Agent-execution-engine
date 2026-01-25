package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"
)

type PolicyDecisionResponse struct {
	Decision struct {
		Allowed           bool   `json:"allowed"`
		SelectedModelTier string `json:"selected_model_tier"`
		HardStop          bool   `json:"hard_stop"`
	} `json:"decision"`
	Reason        string `json:"reason"`
	PolicyVersion string `json:"policy_version"`
}

func evaluatePolicyHandler(w http.ResponseWriter, r *http.Request) {
	// Always enforce method
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Always respect request cancellation
	ctx := r.Context()
	select {
	case <-ctx.Done():
		http.Error(w, "request cancelled", http.StatusRequestTimeout)
		return
	default:
	}

	// We don't parse the body yet — this is intentional.
	// For now, we just acknowledge the request exists.
	log.Println("Received policy evaluation request")

	response := PolicyDecisionResponse{
		Reason:        "default_allow",
		PolicyVersion: "v1.0",
	}

	response.Decision.Allowed = true
	response.Decision.SelectedModelTier = "efficient"
	response.Decision.HardStop = false

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)

	if err := json.NewEncoder(w).Encode(response); err != nil {
		log.Printf("failed to encode response: %v", err)
	}
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/policy/evaluate", evaluatePolicyHandler)

	server := &http.Server{
		Addr:         ":" + port,
		Handler:      mux,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 5 * time.Second,
		IdleTimeout:  30 * time.Second,
	}

	log.Printf("Policy Engine listening on :%s", port)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("server failed: %v", err)
	}
}
