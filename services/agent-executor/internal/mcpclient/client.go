// Package mcpclient provides an HTTP client for the MCP server's tool API.
//
// When MCP_SERVER_URL is set, the agent-executor uses this client to:
//   - fetch the list of available tools before each execute-type step
//   - dispatch tool calls that the LLM requests during a step
//
// If MCP_SERVER_URL is not set, DefaultClient remains nil and the agent
// runs without tool support (plain text generation only).
package mcpclient

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"
)

// Property describes a single field in a tool's input schema.
type Property struct {
	Type        string `json:"type"`
	Description string `json:"description,omitempty"`
}

// JSONSchema is the parameter schema for a tool, matching the MCP ToolDefinition shape.
type JSONSchema struct {
	Type       string              `json:"type"`
	Properties map[string]Property `json:"properties,omitempty"`
	Required   []string            `json:"required,omitempty"`
}

// ToolDefinition describes a single tool exposed by the MCP server.
type ToolDefinition struct {
	Name        string     `json:"name"`
	Description string     `json:"description"`
	InputSchema JSONSchema `json:"inputSchema"`
}

// ToolCallRequest is the body sent to POST /tools/call.
type ToolCallRequest struct {
	Name      string         `json:"name"`
	Arguments map[string]any `json:"arguments,omitempty"`
}

// ContentBlock is one piece of content in a tool result.
type ContentBlock struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

// ToolCallResult is the response from POST /tools/call.
type ToolCallResult struct {
	Content []ContentBlock `json:"content"`
	IsError bool           `json:"isError,omitempty"`
}

// Client is an HTTP client for the MCP server tool API.
type Client struct {
	baseURL    string
	httpClient *http.Client
}

// New creates a Client targeting the given baseURL (e.g. "http://mcp-server:8083").
func New(baseURL string) *Client {
	return &Client{
		baseURL:    strings.TrimRight(baseURL, "/"),
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
}

// DefaultClient is set by InitDefault when MCP_SERVER_URL is configured.
var DefaultClient *Client

// InitDefault reads MCP_SERVER_URL and initialises DefaultClient.
// Safe to call multiple times; no-op after the first call.
func InitDefault() {
	if DefaultClient != nil {
		return
	}
	url := os.Getenv("MCP_SERVER_URL")
	if url == "" {
		return // not configured; tool dispatch disabled
	}
	DefaultClient = New(url)
}

// Available returns true when an MCP server URL was provided at startup.
func Available() bool {
	return DefaultClient != nil
}

// ListTools fetches the full list of available tool definitions from the MCP server.
func (c *Client) ListTools() ([]ToolDefinition, error) {
	resp, err := c.httpClient.Get(c.baseURL + "/tools")
	if err != nil {
		return nil, fmt.Errorf("mcpclient: list tools: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("mcpclient: list tools: status %d", resp.StatusCode)
	}

	var tools []ToolDefinition
	if err := json.NewDecoder(resp.Body).Decode(&tools); err != nil {
		return nil, fmt.Errorf("mcpclient: decode tools: %w", err)
	}
	return tools, nil
}

// CallTool invokes the named tool with the given arguments on the MCP server.
func (c *Client) CallTool(name string, args map[string]any) (ToolCallResult, error) {
	body, err := json.Marshal(ToolCallRequest{Name: name, Arguments: args})
	if err != nil {
		return ToolCallResult{}, fmt.Errorf("mcpclient: marshal: %w", err)
	}

	resp, err := c.httpClient.Post(
		c.baseURL+"/tools/call",
		"application/json",
		bytes.NewBuffer(body),
	)
	if err != nil {
		return ToolCallResult{}, fmt.Errorf("mcpclient: call tool %q: %w", name, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return ToolCallResult{}, fmt.Errorf("mcpclient: call tool %q: status %d", name, resp.StatusCode)
	}

	var result ToolCallResult
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return ToolCallResult{}, fmt.Errorf("mcpclient: decode result: %w", err)
	}
	return result, nil
}
