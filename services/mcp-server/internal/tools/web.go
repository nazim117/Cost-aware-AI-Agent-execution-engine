package tools

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"

	"mcp-server/internal/mcp"
)

var webClient = &http.Client{Timeout: 15 * time.Second}

// webSearch queries the DuckDuckGo Instant Answer API (no key required).
// It returns titles, URLs, and snippets for the top results.
func webSearch(args map[string]any) (mcp.ToolCallResult, error) {
	query, errResult, err := requireString(args, "query")
	if errResult != nil {
		return *errResult, err
	}
	limit := int(optionalFloat(args, "limit", 5))

	// DuckDuckGo Instant Answer API — free, no key needed.
	apiURL := fmt.Sprintf(
		"https://api.duckduckgo.com/?q=%s&format=json&no_redirect=1&no_html=1&skip_disambig=1",
		url.QueryEscape(query),
	)

	resp, err := webClient.Get(apiURL)
	if err != nil {
		return textErr(fmt.Sprintf("search request failed: %v", err))
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return textErr(fmt.Sprintf("read response failed: %v", err))
	}

	var raw map[string]any
	if err := json.Unmarshal(body, &raw); err != nil {
		return textErr(fmt.Sprintf("parse response failed: %v", err))
	}

	// Extract related topics as the search results.
	type result struct {
		Title   string `json:"title"`
		URL     string `json:"url"`
		Snippet string `json:"snippet"`
	}
	var results []result

	// Abstract (top answer)
	if abstract, ok := raw["Abstract"].(string); ok && abstract != "" {
		results = append(results, result{
			Title:   fmt.Sprintf("%v", raw["Heading"]),
			URL:     fmt.Sprintf("%v", raw["AbstractURL"]),
			Snippet: abstract,
		})
	}

	// Related topics
	if topics, ok := raw["RelatedTopics"].([]any); ok {
		for _, t := range topics {
			if len(results) >= limit {
				break
			}
			m, ok := t.(map[string]any)
			if !ok {
				continue
			}
			text, _ := m["Text"].(string)
			firstURL, _ := m["FirstURL"].(string)
			if text == "" {
				continue
			}
			results = append(results, result{
				Title:   text,
				URL:     firstURL,
				Snippet: text,
			})
		}
	}

	return textResult(map[string]any{
		"query":   query,
		"count":   len(results),
		"results": results,
	})
}

// webFetch fetches the raw text content of a URL.
// It strips nothing — the agent receives the raw body. For HTML pages the
// agent should extract what it needs; for JSON APIs it will parse cleanly.
func webFetch(args map[string]any) (mcp.ToolCallResult, error) {
	rawURL, errResult, err := requireString(args, "url")
	if errResult != nil {
		return *errResult, err
	}

	// Basic URL validation
	if _, err := url.ParseRequestURI(rawURL); err != nil {
		return textErr(fmt.Sprintf("invalid URL %q: %v", rawURL, err))
	}

	resp, err := webClient.Get(rawURL)
	if err != nil {
		return textErr(fmt.Sprintf("fetch failed: %v", err))
	}
	defer resp.Body.Close()

	// Cap at 100 KB to avoid overwhelming the agent context.
	const maxBytes = 100 * 1024
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBytes))
	if err != nil {
		return textErr(fmt.Sprintf("read body failed: %v", err))
	}

	return textResult(map[string]any{
		"url":       rawURL,
		"status":    resp.StatusCode,
		"body":      string(body),
		"truncated": len(body) == maxBytes,
	})
}

func webDefinitions() []mcp.ToolDefinition {
	return []mcp.ToolDefinition{
		{
			Name:        "web_search",
			Description: "Search the web for a query and return titles, URLs, and snippets for the top results. Use this during the execute step when the agent needs current information.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"query": {Type: "string", Description: "The search query."},
					"limit": {Type: "number", Description: "Maximum number of results to return (default 5, max 10)."},
				},
				Required: []string{"query"},
			},
		},
		{
			Name:        "web_fetch",
			Description: "Fetch the content of a URL and return it as text. Use this to read a specific page or API endpoint found via web_search. Responses are capped at 100 KB.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"url": {Type: "string", Description: "The full URL to fetch (must include https://)."},
				},
				Required: []string{"url"},
			},
		},
	}
}
