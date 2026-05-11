package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"

	"github.com/joho/godotenv"
	"mcp-server/internal/mcp"
	"mcp-server/internal/tools"
)

// findEnvFile walks up from the working directory looking for a .env file.
func findEnvFile() string {
	dir, err := os.Getwd()
	if err != nil {
		return ""
	}
	for {
		candidate := filepath.Join(dir, ".env")
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
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
	log.SetOutput(os.Stderr)
	log.SetPrefix("[mcp] ")

	// Load .env by walking up from cwd until we find it.
	if envFile := findEnvFile(); envFile != "" {
		if err := godotenv.Load(envFile); err != nil {
			log.Printf("failed to load %s: %v", envFile, err)
		} else {
			log.Printf("loaded env from %s", envFile)
		}
	}

	registry := tools.NewRegistry()

	// stdio transport: used when launched as a child process by an MCP client
	// (e.g. Claude Code, Claude Desktop). Set TRANSPORT=stdio to enable.
	if os.Getenv("TRANSPORT") == "stdio" {
		server := mcp.NewServer(registry)
		if err := server.Serve(os.Stdin, os.Stdout); err != nil {
			log.Fatal(err)
		}
		return
	}

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
