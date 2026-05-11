package tools

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"

	"mcp-server/internal/mcp"
)

const githubAPIBase = "https://api.github.com"

type githubClient struct {
	token string
}

func githubIsConfigured() bool {
	return os.Getenv("GITHUB_TOKEN") != ""
}

func newGitHubClient() *githubClient {
	return &githubClient{token: os.Getenv("GITHUB_TOKEN")}
}

func (c *githubClient) do(method, path string, body *strings.Reader) ([]byte, int, error) {
	var req *http.Request
	var err error
	if body != nil {
		req, err = http.NewRequest(method, githubAPIBase+path, body)
	} else {
		req, err = http.NewRequest(method, githubAPIBase+path, nil)
	}
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 100*1024))
	return raw, resp.StatusCode, nil
}

func (c *githubClient) listIssues(args map[string]any) (mcp.ToolCallResult, error) {
	repo, errResult, err := requireString(args, "repo")
	if errResult != nil {
		return *errResult, err
	}
	state := optionalString(args, "state", "open")
	limit := int(optionalFloat(args, "limit", 20))
	if limit > 100 {
		limit = 100
	}

	path := fmt.Sprintf("/repos/%s/issues?state=%s&per_page=%d", repo, state, limit)
	raw, status, err := c.do("GET", path, nil)
	if err != nil {
		return textErr(fmt.Sprintf("github request failed: %v", err))
	}
	if status != 200 {
		return textErr(fmt.Sprintf("github error %d: %s", status, string(raw)))
	}

	var issues []struct {
		Number      int                                         `json:"number"`
		Title       string                                      `json:"title"`
		State       string                                      `json:"state"`
		HTMLURL     string                                      `json:"html_url"`
		PullRequest *struct{}                                   `json:"pull_request"`
		Assignee    *struct{ Login string `json:"login"` }      `json:"assignee"`
		CreatedAt   string                                      `json:"created_at"`
	}
	if err := json.Unmarshal(raw, &issues); err != nil {
		return textErr(fmt.Sprintf("parse response failed: %v", err))
	}

	type issueOut struct {
		Number    int    `json:"number"`
		Title     string `json:"title"`
		State     string `json:"state"`
		Type      string `json:"type"`
		Assignee  string `json:"assignee,omitempty"`
		URL       string `json:"url"`
		CreatedAt string `json:"created_at"`
	}
	out := make([]issueOut, len(issues))
	for i, iss := range issues {
		assignee := ""
		if iss.Assignee != nil {
			assignee = iss.Assignee.Login
		}
		kind := "issue"
		if iss.PullRequest != nil {
			kind = "pull_request"
		}
		out[i] = issueOut{
			Number:    iss.Number,
			Title:     iss.Title,
			State:     iss.State,
			Type:      kind,
			Assignee:  assignee,
			URL:       iss.HTMLURL,
			CreatedAt: iss.CreatedAt,
		}
	}
	return textResult(map[string]any{"count": len(out), "issues": out})
}

func (c *githubClient) getIssue(args map[string]any) (mcp.ToolCallResult, error) {
	repo, errResult, err := requireString(args, "repo")
	if errResult != nil {
		return *errResult, err
	}
	numberF, ok := args["number"].(float64)
	if !ok || numberF <= 0 {
		return textErr(`missing required argument: "number"`)
	}
	number := int(numberF)

	path := fmt.Sprintf("/repos/%s/issues/%d", repo, number)
	raw, status, err := c.do("GET", path, nil)
	if err != nil {
		return textErr(fmt.Sprintf("github request failed: %v", err))
	}
	if status != 200 {
		return textErr(fmt.Sprintf("github error %d: %s", status, string(raw)))
	}

	var iss struct {
		Number      int                                         `json:"number"`
		Title       string                                      `json:"title"`
		Body        string                                      `json:"body"`
		State       string                                      `json:"state"`
		HTMLURL     string                                      `json:"html_url"`
		PullRequest *struct{}                                   `json:"pull_request"`
		Assignee    *struct{ Login string `json:"login"` }      `json:"assignee"`
		Labels      []struct{ Name string `json:"name"` }       `json:"labels"`
		CreatedAt   string                                      `json:"created_at"`
		UpdatedAt   string                                      `json:"updated_at"`
	}
	if err := json.Unmarshal(raw, &iss); err != nil {
		return textErr(fmt.Sprintf("parse response failed: %v", err))
	}

	assignee := ""
	if iss.Assignee != nil {
		assignee = iss.Assignee.Login
	}
	labels := make([]string, len(iss.Labels))
	for i, l := range iss.Labels {
		labels[i] = l.Name
	}
	kind := "issue"
	if iss.PullRequest != nil {
		kind = "pull_request"
	}

	return textResult(map[string]any{
		"number":     iss.Number,
		"type":       kind,
		"title":      iss.Title,
		"body":       iss.Body,
		"state":      iss.State,
		"assignee":   assignee,
		"labels":     labels,
		"url":        iss.HTMLURL,
		"created_at": iss.CreatedAt,
		"updated_at": iss.UpdatedAt,
	})
}

func (c *githubClient) addComment(args map[string]any) (mcp.ToolCallResult, error) {
	repo, errResult, err := requireString(args, "repo")
	if errResult != nil {
		return *errResult, err
	}
	numberF, ok := args["number"].(float64)
	if !ok || numberF <= 0 {
		return textErr(`missing required argument: "number"`)
	}
	number := int(numberF)
	body, errResult, err := requireString(args, "body")
	if errResult != nil {
		return *errResult, err
	}

	payload, _ := json.Marshal(map[string]string{"body": body})
	path := fmt.Sprintf("/repos/%s/issues/%d/comments", repo, number)
	raw, status, err := c.do("POST", path, strings.NewReader(string(payload)))
	if err != nil {
		return textErr(fmt.Sprintf("github request failed: %v", err))
	}
	if status != 201 {
		return textErr(fmt.Sprintf("github error %d: %s", status, string(raw)))
	}

	var result struct {
		ID        int    `json:"id"`
		HTMLURL   string `json:"html_url"`
		CreatedAt string `json:"created_at"`
	}
	if err := json.Unmarshal(raw, &result); err != nil {
		return textErr(fmt.Sprintf("parse response failed: %v", err))
	}

	return textResult(map[string]any{
		"comment_id": result.ID,
		"url":        result.HTMLURL,
		"created_at": result.CreatedAt,
	})
}

func githubDefinitions() []mcp.ToolDefinition {
	return []mcp.ToolDefinition{
		{
			Name:        "github_list_issues",
			Description: `List issues and pull requests in a GitHub repository. Returns number, title, state, type (issue/pull_request), assignee, and URL for each item.`,
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"repo":  {Type: "string", Description: `Repository in "owner/repo" format, e.g. "octocat/hello-world".`},
					"state": {Type: "string", Description: `Filter by state: "open", "closed", or "all". Defaults to "open".`},
					"limit": {Type: "number", Description: "Maximum number of results to return (1–100, default 20)."},
				},
				Required: []string{"repo"},
			},
		},
		{
			Name:        "github_get_issue",
			Description: "Fetch full details for a single GitHub issue or pull request by its number. Returns title, body, state, assignee, labels, and timestamps.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"repo":   {Type: "string", Description: `Repository in "owner/repo" format.`},
					"number": {Type: "number", Description: "Issue or pull request number."},
				},
				Required: []string{"repo", "number"},
			},
		},
		{
			Name:        "github_add_comment",
			Description: "Post a comment on a GitHub issue or pull request. Returns the new comment ID and URL.",
			InputSchema: mcp.JSONSchema{
				Type: "object",
				Properties: map[string]mcp.Property{
					"repo":   {Type: "string", Description: `Repository in "owner/repo" format.`},
					"number": {Type: "number", Description: "Issue or pull request number."},
					"body":   {Type: "string", Description: "Comment text to post."},
				},
				Required: []string{"repo", "number", "body"},
			},
		},
	}
}
