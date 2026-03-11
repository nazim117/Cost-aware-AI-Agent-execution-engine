package tools

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"mcp-server/internal/mcp"
)

var httpClient = &http.Client{Timeout: 30 * time.Second}

// httpRequest makes a generic outbound HTTP call so the agent can hit any
// external API without needing a dedicated tool per service.
func httpRequest(args map[string]any) (mcp.ToolCallResult, error) {
	rawURL, errResult, err := requireString(args, "url")
	if errResult != nil {
		return *errResult, err
	}
	method := strings.ToUpper(optionalString(args, "method", "GET"))

	// Build request body.
	var bodyReader io.Reader
	if bodyArg, ok := args["body"]; ok && bodyArg != nil {
		switch v := bodyArg.(type) {
		case string:
			bodyReader = strings.NewReader(v)
		default:
			// Caller passed an object — serialise it to JSON.
			b, err := json.Marshal(v)
			if err != nil {
				return textErr(fmt.Sprintf("cannot serialise body: %v", err))
			}
			bodyReader = bytes.NewReader(b)
		}
	}

	req, err := http.NewRequest(method, rawURL, bodyReader)
	if err != nil {
		return textErr(fmt.Sprintf("build request failed: %v", err))
	}

	// Apply caller-supplied headers.
	if hdrs, ok := args["headers"].(map[string]any); ok {
		for k, v := range hdrs {
			req.Header.Set(k, fmt.Sprintf("%v", v))
		}
	}

	// Default Content-Type for requests with a body.
	if bodyReader != nil && req.Header.Get("Content-Type") == "" {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := httpClient.Do(req)
	if err != nil {
		return textErr(fmt.Sprintf("request failed: %v", err))
	}
	defer resp.Body.Close()

	// Cap response at 100 KB.
	const maxBytes = 100 * 1024
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBytes))
	if err != nil {
		return textErr(fmt.Sprintf("read response failed: %v", err))
	}

	// Try to parse the response as JSON for a cleaner result.
	var parsedBody any
	if err := json.Unmarshal(body, &parsedBody); err != nil {
		// Not JSON — return as plain string.
		parsedBody = string(body)
	}

	// Collect response headers.
	headers := make(map[string]string, len(resp.Header))
	for k, vs := range resp.Header {
		headers[k] = strings.Join(vs, ", ")
	}

	return textResult(map[string]any{
		"status":    resp.StatusCode,
		"headers":   headers,
		"body":      parsedBody,
		"truncated": len(body) == maxBytes,
	})
}

func httpDefinitions() []mcp.ToolDefinition {
	return []mcp.ToolDefinition{
		{
			Name:        "http_request",
			Description: "Make an HTTP request to any external API and return the status code, response headers, and body. The body is automatically parsed as JSON if possible. Use this to call REST APIs, webhooks, or any HTTP service during the execute step.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"url":     {Type: "string", Description: "The full URL to call (must include https://)."},
					"method":  {Type: "string", Description: `HTTP method: "GET", "POST", "PUT", "PATCH", "DELETE". Defaults to "GET".`},
					"headers": {Type: "object", Description: "Optional map of request headers (e.g. {\"Authorization\": \"Bearer token\"})."},
					"body":    {Type: "string", Description: "Optional request body. Pass a JSON string or a plain string. If an object is passed it will be serialised to JSON automatically."},
				},
				Required: []string{"url"},
			},
		},
	}
}
