package tools

import (
	"encoding/json"
	"fmt"

	"mcp-server/internal/mcp"
)

func textResult(v any) (mcp.ToolCallResult, error) {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return mcp.ToolCallResult{}, fmt.Errorf("marshal result: %w", err)
	}
	return mcp.ToolCallResult{
		Content: []mcp.ContentBlock{{Type: "text", Text: string(b)}},
	}, nil
}

func textErr(msg string) (mcp.ToolCallResult, error) {
	return mcp.ToolCallResult{
		Content: []mcp.ContentBlock{{Type: "text", Text: msg}},
		IsError: true,
	}, nil
}

func requireString(args map[string]any, key string) (string, *mcp.ToolCallResult, error) {
	v, ok := args[key].(string)
	if !ok || v == "" {
		r, err := textErr(fmt.Sprintf("missing required argument: %q", key))
		return "", &r, err
	}
	return v, nil, nil
}

func optionalString(args map[string]any, key, def string) string {
	if v, ok := args[key].(string); ok && v != "" {
		return v
	}
	return def
}

func optionalFloat(args map[string]any, key string, def float64) float64 {
	if v, ok := args[key].(float64); ok {
		return v
	}
	return def
}
