package tools

import (
	"fmt"
	"sync"

	"mcp-server/internal/mcp"
)

// memoryStore is a thread-safe in-process key/value store.
// It is scoped to the lifetime of the MCP server process, so it survives
// across steps within a single agent run but resets on restart.
type memoryStore struct {
	mu   sync.RWMutex
	data map[string]string
}

func newMemoryStore() *memoryStore {
	return &memoryStore{data: map[string]string{}}
}

// set stores key=value, overwriting any previous value.
func (m *memoryStore) set(args map[string]any) (mcp.ToolCallResult, error) {
	key, errResult, err := requireString(args, "key")
	if errResult != nil {
		return *errResult, err
	}
	value, errResult, err := requireString(args, "value")
	if errResult != nil {
		return *errResult, err
	}

	m.mu.Lock()
	m.data[key] = value
	m.mu.Unlock()

	return textResult(map[string]any{"ok": true, "key": key})
}

// get retrieves the value for a key. Returns an error result if not found.
func (m *memoryStore) get(args map[string]any) (mcp.ToolCallResult, error) {
	key, errResult, err := requireString(args, "key")
	if errResult != nil {
		return *errResult, err
	}

	m.mu.RLock()
	value, exists := m.data[key]
	m.mu.RUnlock()

	if !exists {
		return textErr(fmt.Sprintf("key not found: %q", key))
	}
	return textResult(map[string]any{"key": key, "value": value})
}

// list returns all stored keys and their values.
func (m *memoryStore) list(_ map[string]any) (mcp.ToolCallResult, error) {
	m.mu.RLock()
	snapshot := make(map[string]string, len(m.data))
	for k, v := range m.data {
		snapshot[k] = v
	}
	m.mu.RUnlock()

	return textResult(map[string]any{
		"count":   len(snapshot),
		"entries": snapshot,
	})
}

// memoryDefinitions returns the MCP tool definitions for the memory tools.
func memoryDefinitions() []mcp.ToolDefinition {
	return []mcp.ToolDefinition{
		{
			Name:        "memory_set",
			Description: "Store a value in the agent's memory under a named key. Use this to persist context between steps — e.g. the user's goal, intermediate results, or a plan. Overwrites any existing value for that key.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"key":   {Type: "string", Description: "The name to store the value under (e.g. \"user_goal\", \"search_results\")."},
					"value": {Type: "string", Description: "The value to store. Serialise complex data as JSON before storing."},
				},
				Required: []string{"key", "value"},
			},
		},
		{
			Name:        "memory_get",
			Description: "Retrieve a previously stored value from the agent's memory by key.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"key": {Type: "string", Description: "The key to look up."},
				},
				Required: []string{"key"},
			},
		},
		{
			Name:        "memory_list",
			Description: "List all keys and values currently stored in the agent's memory. Useful at the start of a step to see what context is already available.",
			InputSchema: mcp.JSONSchema{
				Type:       "object",
				Properties: map[string]mcp.Property{},
				Required:   []string{},
			},
		},
	}
}
