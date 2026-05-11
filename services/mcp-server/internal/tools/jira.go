package tools

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"

	"mcp-server/internal/mcp"
)

type jiraClient struct {
	baseURL string
	email   string
	token   string
}

func jiraIsConfigured() bool {
	return os.Getenv("JIRA_BASE_URL") != "" &&
		os.Getenv("JIRA_EMAIL") != "" &&
		os.Getenv("JIRA_API_TOKEN") != ""
}

func newJiraClient() *jiraClient {
	return &jiraClient{
		baseURL: strings.TrimRight(os.Getenv("JIRA_BASE_URL"), "/"),
		email:   os.Getenv("JIRA_EMAIL"),
		token:   os.Getenv("JIRA_API_TOKEN"),
	}
}

func (c *jiraClient) get(path string) ([]byte, int, error) {
	req, err := http.NewRequest("GET", c.baseURL+path, nil)
	if err != nil {
		return nil, 0, err
	}
	req.SetBasicAuth(c.email, c.token)
	req.Header.Set("Accept", "application/json")
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 100*1024))
	return body, resp.StatusCode, nil
}

func (c *jiraClient) post(path string, payload any) ([]byte, int, error) {
	b, _ := json.Marshal(payload)
	req, err := http.NewRequest("POST", c.baseURL+path, strings.NewReader(string(b)))
	if err != nil {
		return nil, 0, err
	}
	req.SetBasicAuth(c.email, c.token)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 100*1024))
	return body, resp.StatusCode, nil
}

func (c *jiraClient) searchIssues(args map[string]any) (mcp.ToolCallResult, error) {
	query, errResult, err := requireString(args, "query")
	if errResult != nil {
		return *errResult, err
	}
	maxResults := int(optionalFloat(args, "max_results", 20))

	path := fmt.Sprintf("/rest/api/3/issue/search?jql=%s&maxResults=%d&fields=summary,status,assignee",
		url.QueryEscape(query), maxResults)

	raw, status, err := c.get(path)
	if err != nil {
		return textErr(fmt.Sprintf("jira request failed: %v", err))
	}
	if status != 200 {
		return textErr(fmt.Sprintf("jira error %d: %s", status, string(raw)))
	}

	var result struct {
		Total  int `json:"total"`
		Issues []struct {
			Key    string `json:"key"`
			Fields struct {
				Summary  string                                             `json:"summary"`
				Status   struct{ Name string `json:"name"` }               `json:"status"`
				Assignee *struct{ DisplayName string `json:"displayName"` } `json:"assignee"`
			} `json:"fields"`
		} `json:"issues"`
	}
	if err := json.Unmarshal(raw, &result); err != nil {
		return textErr(fmt.Sprintf("parse response failed: %v", err))
	}

	type issueOut struct {
		Key      string `json:"key"`
		Summary  string `json:"summary"`
		Status   string `json:"status"`
		Assignee string `json:"assignee,omitempty"`
		URL      string `json:"url"`
	}
	out := make([]issueOut, len(result.Issues))
	for i, iss := range result.Issues {
		assignee := ""
		if iss.Fields.Assignee != nil {
			assignee = iss.Fields.Assignee.DisplayName
		}
		out[i] = issueOut{
			Key:      iss.Key,
			Summary:  iss.Fields.Summary,
			Status:   iss.Fields.Status.Name,
			Assignee: assignee,
			URL:      c.baseURL + "/browse/" + iss.Key,
		}
	}
	return textResult(map[string]any{"total": result.Total, "issues": out})
}

func (c *jiraClient) getIssue(args map[string]any) (mcp.ToolCallResult, error) {
	key, errResult, err := requireString(args, "key")
	if errResult != nil {
		return *errResult, err
	}

	path := fmt.Sprintf("/rest/api/3/issue/%s?fields=summary,description,status,assignee,priority,created,updated",
		url.PathEscape(key))

	raw, status, err := c.get(path)
	if err != nil {
		return textErr(fmt.Sprintf("jira request failed: %v", err))
	}
	if status != 200 {
		return textErr(fmt.Sprintf("jira error %d: %s", status, string(raw)))
	}

	var result struct {
		Key    string `json:"key"`
		Fields struct {
			Summary     string                                             `json:"summary"`
			Description any                                                `json:"description"`
			Status      struct{ Name string `json:"name"` }               `json:"status"`
			Assignee    *struct{ DisplayName string `json:"displayName"` } `json:"assignee"`
			Priority    *struct{ Name string `json:"name"` }              `json:"priority"`
			Created     string                                             `json:"created"`
			Updated     string                                             `json:"updated"`
		} `json:"fields"`
	}
	if err := json.Unmarshal(raw, &result); err != nil {
		return textErr(fmt.Sprintf("parse response failed: %v", err))
	}

	assignee, priority := "", ""
	if result.Fields.Assignee != nil {
		assignee = result.Fields.Assignee.DisplayName
	}
	if result.Fields.Priority != nil {
		priority = result.Fields.Priority.Name
	}

	return textResult(map[string]any{
		"key":         result.Key,
		"summary":     result.Fields.Summary,
		"description": adfToText(result.Fields.Description),
		"status":      result.Fields.Status.Name,
		"assignee":    assignee,
		"priority":    priority,
		"url":         c.baseURL + "/browse/" + result.Key,
		"created":     result.Fields.Created,
		"updated":     result.Fields.Updated,
	})
}

func (c *jiraClient) addComment(args map[string]any) (mcp.ToolCallResult, error) {
	key, errResult, err := requireString(args, "key")
	if errResult != nil {
		return *errResult, err
	}
	body, errResult, err := requireString(args, "body")
	if errResult != nil {
		return *errResult, err
	}

	// Jira requires plain text wrapped in Atlassian Document Format (ADF).
	payload := map[string]any{
		"body": map[string]any{
			"type":    "doc",
			"version": 1,
			"content": []any{
				map[string]any{
					"type": "paragraph",
					"content": []any{
						map[string]any{"type": "text", "text": body},
					},
				},
			},
		},
	}

	path := fmt.Sprintf("/rest/api/3/issue/%s/comment", url.PathEscape(key))
	raw, status, err := c.post(path, payload)
	if err != nil {
		return textErr(fmt.Sprintf("jira request failed: %v", err))
	}
	if status != 201 {
		return textErr(fmt.Sprintf("jira error %d: %s", status, string(raw)))
	}

	var result struct {
		ID      string `json:"id"`
		Created string `json:"created"`
	}
	if err := json.Unmarshal(raw, &result); err != nil {
		return textErr(fmt.Sprintf("parse response failed: %v", err))
	}

	return textResult(map[string]any{
		"comment_id": result.ID,
		"url":        c.baseURL + "/browse/" + key + "?focusedCommentId=" + result.ID,
		"created_at": result.Created,
	})
}

// adfToText extracts plain text from Atlassian Document Format (ADF).
func adfToText(node any) string {
	if node == nil {
		return ""
	}
	var sb strings.Builder
	extractADFNode(node, &sb)
	return strings.TrimSpace(sb.String())
}

func extractADFNode(node any, sb *strings.Builder) {
	m, ok := node.(map[string]any)
	if !ok {
		return
	}
	if t, _ := m["type"].(string); t == "text" {
		if text, _ := m["text"].(string); text != "" {
			sb.WriteString(text)
		}
		return
	}
	if children, ok := m["content"].([]any); ok {
		for _, child := range children {
			extractADFNode(child, sb)
		}
		sb.WriteString(" ")
	}
}

func jiraDefinitions() []mcp.ToolDefinition {
	return []mcp.ToolDefinition{
		{
			Name:        "jira_search_issues",
			Description: "Search Jira issues using a JQL query. Returns matching issues with their key, summary, status, assignee, and URL.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"query":       {Type: "string", Description: `JQL query string, e.g. "project = PROJ AND status = 'In Progress' ORDER BY created DESC".`},
					"max_results": {Type: "number", Description: "Maximum number of issues to return (1–50, default 20)."},
				},
				Required: []string{"query"},
			},
		},
		{
			Name:        "jira_get_issue",
			Description: `Fetch full details for a single Jira issue by its key (e.g. "PROJ-123"). Returns summary, description, status, assignee, priority, and timestamps.`,
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"key": {Type: "string", Description: `Jira issue key, e.g. "PROJ-123".`},
				},
				Required: []string{"key"},
			},
		},
		{
			Name:        "jira_add_comment",
			Description: "Post a comment on a Jira issue. Returns the new comment ID and URL.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"key":  {Type: "string", Description: `Jira issue key, e.g. "PROJ-123".`},
					"body": {Type: "string", Description: "Comment text to post."},
				},
				Required: []string{"key", "body"},
			},
		},
	}
}
