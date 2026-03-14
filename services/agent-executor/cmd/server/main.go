package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"

	"agent-executor/internal/handlers"
	"agent-executor/internal/metrics"
)

func handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status": "healthy",
	})
}

func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8081"
	}

	log.Printf("[main] GATEWAY_URL=%s", os.Getenv("GATEWAY_URL"))
	log.Printf("[main] MCP_SERVER_URL=%s", os.Getenv("MCP_SERVER_URL"))
	log.Printf("[main] POLICY_ENGINE_URL=%s", os.Getenv("POLICY_ENGINE_URL"))

	mux := http.NewServeMux()
	m := metrics.New()

	mux.HandleFunc("/agent/run", handlers.RunAgentHandler(m))
	mux.HandleFunc("/metrics", handlers.MetricsHandler(m))
	mux.HandleFunc("/health", handleHealth)

	// ReadTimeout covers reading the request body.
	// WriteTimeout must be long enough for a full agent run:
	// each LLM step can take 10-30s, and a graph may have many steps.
	// Set a generous ceiling; real budget/latency enforcement happens
	// inside the policy engine and runner, not at the HTTP layer.
	server := &http.Server{
		Addr:         ":" + port,
		Handler:      corsMiddleware(mux), // ← CORS wraps the mux here
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 5 * time.Minute,
	}

	log.Printf("Agent Executor listening on :%s", port)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("server failed: %v", err)
	}
}
