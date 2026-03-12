// Package gatewayclient provides an HTTP client that routes LLM chat requests
// through the PII-scanning gateway service instead of calling the LLM directly.
//
// When GATEWAY_URL is set, all LLM calls from the agent-executor are proxied
// through the gateway, which:
//   - scans every message for PII (SSN, email, credit card, phone)
//   - blocks the request (HTTP 403) if PII is found and the gateway is in block mode
//   - redacts PII and forwards the cleaned request to the upstream LLM otherwise
//   - emits a structured audit log entry for every request
//
// If GATEWAY_URL is not set the caller should fall back to calling the LLM
// directly; this package never silently drops requests.
package gatewayclient

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// --- Tool calling types ---

// Property describes a single field in a JSON Schema.
type Property struct {
	Type        string `json:"type"`
	Description string `json:"description,omitempty"`
}

// JSONSchema is the parameter schema sent to the LLM for a tool.
type JSONSchema struct {
	Type       string              `json:"type"`
	Properties map[string]Property `json:"properties,omitempty"`
	Required   []string            `json:"required,omitempty"`
}

// ToolFunction is the function definition inside a Tool.
type ToolFunction struct {
	Name        string     `json:"name"`
	Description string     `json:"description,omitempty"`
	Parameters  JSONSchema `json:"parameters"`
}

// Tool is one entry in the tools array of a chat request.
type Tool struct {
	Type     string       `json:"type"` // "function"
	Function ToolFunction `json:"function"`
}

// ToolCallFunction holds the LLM's chosen function name and JSON-encoded args.
type ToolCallFunction struct {
	Name      string `json:"name"`
	Arguments string `json:"arguments"` // JSON string
}

// ToolCall is one function-call requested by the LLM in a response.
type ToolCall struct {
	ID       string           `json:"id"`
	Type     string           `json:"type"` // "function"
	Function ToolCallFunction `json:"function"`
}

// --- Chat types ---

// Message mirrors the OpenAI chat message shape used by both the gateway and
// the upstream LLM API. Content is omitempty because assistant messages that
// request tool calls may have an empty/null content field.
type Message struct {
	Role       string     `json:"role"`
	Content    string     `json:"content,omitempty"`
	ToolCalls  []ToolCall `json:"tool_calls,omitempty"`
	ToolCallID string     `json:"tool_call_id,omitempty"`
	Name       string     `json:"name,omitempty"`
}

// ChatRequest is the payload sent to the gateway's /v1/chat/completions endpoint.
type ChatRequest struct {
	Model       string    `json:"model"`
	Messages    []Message `json:"messages"`
	Temperature float64   `json:"temperature,omitempty"`
	MaxTokens   int       `json:"max_tokens,omitempty"`
	Tools       []Tool    `json:"tools,omitempty"`
	ToolChoice  string    `json:"tool_choice,omitempty"`
}

// ChatResponse is the response returned by the gateway (which proxies the
// upstream LLM response verbatim).
type ChatResponse struct {
	ID      string   `json:"id"`
	Object  string   `json:"object"`
	Created int64    `json:"created"`
	Model   string   `json:"model"`
	Choices []Choice `json:"choices"`
	Usage   Usage    `json:"usage"`
}

// Choice holds one completion candidate.
type Choice struct {
	Index        int     `json:"index"`
	Message      Message `json:"message"`
	FinishReason string  `json:"finish_reason"`
}

// Usage reports token consumption for the request.
type Usage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

// PIIBlockedError is returned when the gateway rejects a request due to PII.
// Callers can type-assert to inspect which PII types were detected.
type PIIBlockedError struct {
	PIITypes []string
	Message  string
}

func (e *PIIBlockedError) Error() string {
	return fmt.Sprintf("gateway blocked request: PII detected (%s): %s",
		strings.Join(e.PIITypes, ", "), e.Message)
}

// Client is an HTTP client for the PII-scanning gateway service.
type Client struct {
	gatewayURL string
	httpClient *http.Client
}

// New creates a Client targeting the given gatewayURL.
// gatewayURL should be the base URL of the gateway service, e.g.
// "http://gateway:8082". The /v1/chat/completions path is appended
// automatically.
func New(gatewayURL string) *Client {
	return &Client{
		gatewayURL: strings.TrimRight(gatewayURL, "/"),
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
}

// DefaultClient is initialised by InitDefault when GATEWAY_URL is set.
// It is nil when no gateway is configured, which the agent-executor uses as
// the signal to fall back to direct LLM calls.
var DefaultClient *Client

const defaultGatewayURL = "http://localhost:8082"

// InitDefault initialises DefaultClient from the GATEWAY_URL environment
// variable. If the variable is not set, DefaultClient is left nil so that
// callers can detect the absence and fall back to the local LLM path.
// Safe to call multiple times; subsequent calls after the first are no-ops.
func InitDefault() {
	if DefaultClient != nil {
		return
	}
	url := os.Getenv("GATEWAY_URL")
	if url == "" {
		return // not configured; caller falls back to direct LLM path
	}
	DefaultClient = New(url)
}

// Available returns true only when GATEWAY_URL was set at startup and
// InitDefault has been called. Use this to decide whether to route through
// the gateway or fall back to a direct LLM call.
func Available() bool {
	return DefaultClient != nil
}

// Chat sends req through the gateway's /v1/chat/completions endpoint.
//
// On success it returns the LLM response that the gateway forwarded.
// If the gateway blocks the request due to PII it returns *PIIBlockedError.
// Any other non-2xx response is returned as a plain error.
func (c *Client) Chat(req ChatRequest) (*ChatResponse, error) {
	endpoint := c.gatewayURL + "/v1/chat/completions"

	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("gatewayclient: marshal request: %w", err)
	}

	httpReq, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewBuffer(body))
	if err != nil {
		return nil, fmt.Errorf("gatewayclient: build request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")

	httpResp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("gatewayclient: send request: %w", err)
	}
	defer httpResp.Body.Close()

	// The gateway returns 403 when it detects PII and blockOnPII=true.
	if httpResp.StatusCode == http.StatusForbidden {
		var errBody struct {
			Error struct {
				Message  string   `json:"message"`
				Type     string   `json:"type"`
				PIITypes []string `json:"pii_types"`
			} `json:"error"`
		}
		raw, _ := io.ReadAll(httpResp.Body)
		_ = json.Unmarshal(raw, &errBody)
		return nil, &PIIBlockedError{
			PIITypes: errBody.Error.PIITypes,
			Message:  errBody.Error.Message,
		}
	}

	if httpResp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(httpResp.Body)
		return nil, fmt.Errorf("gatewayclient: gateway returned status %d: %s",
			httpResp.StatusCode, string(raw))
	}

	var resp ChatResponse
	if err := json.NewDecoder(httpResp.Body).Decode(&resp); err != nil {
		return nil, fmt.Errorf("gatewayclient: decode response: %w", err)
	}

	return &resp, nil
}
