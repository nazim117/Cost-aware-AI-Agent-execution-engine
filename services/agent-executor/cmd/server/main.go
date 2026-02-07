package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"agent-executor/internal/handlers"
	"agent-executor/internal/metrics"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8081"
	}

	mux := http.NewServeMux()
	m := metrics.New()

	mux.HandleFunc("/agent/run", handlers.RunAgentHandler(m))
	mux.HandleFunc("/metrics", handlers.MetricsHandler(m))

	server := &http.Server{
		Addr:         ":" + port,
		Handler:      mux,
		ReadTimeout:  5 * time.Second,
		WriteTimeout: 5 * time.Second,
	}

	log.Printf("Agent Executor listening on :%s", port)
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("server failed: %v", err)
	}
}
