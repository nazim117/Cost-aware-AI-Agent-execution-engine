package main

import (
	"log"
	"os"

	"mcp-server/internal/mcp"
	"mcp-server/internal/tools"
)

func main() {
	log.SetOutput(os.Stderr)
	log.SetPrefix("[mcp] ")

	srv := mcp.NewServer(tools.NewRegistry())
	if err := srv.Serve(os.Stdin, os.Stdout); err != nil {
		log.Fatalf("server error: %v", err)
	}
}
