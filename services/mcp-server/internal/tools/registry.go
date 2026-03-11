// Package tools provides the MCP tools available to the agent during its
// plan → execute → summarize loop.
//
// Tools are grouped by concern:
//   - memory  — cross-step key/value store (in-process, survives within a run)
//   - web     — search and page fetching
//   - files   — read/write/list on the local filesystem
//   - http    — generic outbound HTTP for any external API
package tools

import (
	"fmt"

	"mcp-server/internal/mcp"
)

// Registry holds shared state (e.g. the memory store) and dispatches tool calls.
type Registry struct {
	mem *memoryStore
}

// NewRegistry constructs a Registry with all tools ready.
func NewRegistry() *Registry {
	return &Registry{mem: newMemoryStore()}
}

// Definitions returns the full tool list sent to MCP clients on tools/list.
func (r *Registry) Definitions() []mcp.ToolDefinition {
	defs := []mcp.ToolDefinition{}
	defs = append(defs, memoryDefinitions()...)
	defs = append(defs, webDefinitions()...)
	defs = append(defs, fileDefinitions()...)
	defs = append(defs, httpDefinitions()...)
	return defs
}

// Call dispatches a tool by name and returns the result.
func (r *Registry) Call(name string, args map[string]any) (mcp.ToolCallResult, error) {
	switch name {
	// memory
	case "memory_set":
		return r.mem.set(args)
	case "memory_get":
		return r.mem.get(args)
	case "memory_list":
		return r.mem.list(args)

	// web
	case "web_search":
		return webSearch(args)
	case "web_fetch":
		return webFetch(args)

	// files
	case "file_read":
		return fileRead(args)
	case "file_write":
		return fileWrite(args)
	case "file_list":
		return fileList(args)

	// http
	case "http_request":
		return httpRequest(args)

	default:
		return mcp.ToolCallResult{}, fmt.Errorf("unknown tool: %q", name)
	}
}
