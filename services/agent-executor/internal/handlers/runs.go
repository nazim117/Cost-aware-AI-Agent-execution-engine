package handlers

import (
	"encoding/json"
	"net/http"
	"strings"

	"agent-executor/internal/runs"
)

// RunsHandler serves GET /runs — returns all buffered runs, newest first.
func RunsHandler(rb *runs.Buffer) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		all := rb.All()
		if all == nil {
			all = []runs.RunEntry{}
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(all)
	}
}

// RunHandler serves GET /runs/{run_id} — returns a single run or 404.
func RunHandler(rb *runs.Buffer) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		id := strings.TrimPrefix(r.URL.Path, "/runs/")
		if id == "" {
			http.Error(w, "missing run id", http.StatusBadRequest)
			return
		}
		entry := rb.Get(id)
		if entry == nil {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(entry)
	}
}
