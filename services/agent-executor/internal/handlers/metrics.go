package handlers

import (
	"encoding/json"
	"net/http"

	"agent-executor/internal/metrics"
)

func MetricsHandler(m *metrics.Metrics) http.HandlerFunc {
	return func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(m)
	}
}
