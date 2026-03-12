package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"

	"mcp-server/internal/mcp"
	"mcp-server/internal/tools"
)

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
	log.SetOutput(os.Stderr)
	log.SetPrefix("[mcp] ")

	registry := tools.NewRegistry()

	port := os.Getenv("PORT")
	if port == "" {
		port = "8083"
	}

	mux := http.NewServeMux()

	// GET /tools — list all available tool definitions.
	mux.HandleFunc("/tools", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(registry.Definitions())
	})

	// POST /tools/call — invoke a tool by name.
	mux.HandleFunc("/tools/call", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		var params mcp.ToolCallParams
		if err := json.NewDecoder(r.Body).Decode(&params); err != nil {
			http.Error(w, "invalid request body: "+err.Error(), http.StatusBadRequest)
			return
		}

		result, err := registry.Call(params.Name, params.Arguments)
		if err != nil {
			result = mcp.ToolCallResult{
				Content: []mcp.ContentBlock{{Type: "text", Text: err.Error()}},
				IsError: true,
			}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(result)
	})

	// GET /health
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "healthy"})
	})

	log.Printf("MCP server listening on :%s (tools: %d)", port, len(registry.Definitions()))
	log.Fatal(http.ListenAndServe(":"+port, corsMiddleware(mux)))
}
