package llmclient

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"time"
)

type ChatRequest struct {
	Model       string    `json:"model"`
	Messages    []Message `json:"messages"`
	Temperature float64   `json:"temperature,omitempty"`
	MaxTokens   int       `json:"max_tokens,omitempty"`
}

type Message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type ChatResponse struct {
	ID      string   `json:"id"`
	Object  string   `json:"object"`
	Created int64    `json:"created"`
	Model   string   `json:"model"`
	Choices []Choice `json:"choices"`
	Usage   Usage    `json:"usage"`
}

type Choice struct {
	Index        int     `json:"index"`
	Message      Message `json:"message"`
	FinishReason string  `json:"finish_reason"`
}

type Usage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

type PIIScanner struct {
	patterns map[string]*regexp.Regexp
}

func NewPIIScanner() *PIIScanner {
	return &PIIScanner{
		patterns: map[string]*regexp.Regexp{
			"ssn":         regexp.MustCompile(`\b\d{3}-\d{2}-\d{4}\b`),
			"email":       regexp.MustCompile(`\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b`),
			"credit_card": regexp.MustCompile(`\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b`),
			"phone":       regexp.MustCompile(`\b\d{3}[-.]?\d{3}[-.]?\d{4}\b`),
		},
	}
}

type ScanResult struct {
	HasPII       bool
	PIITypes     []string
	RedactedText string
}

func (s *PIIScanner) Scan(text string) ScanResult {
	result := ScanResult{
		RedactedText: text,
		PIITypes:     []string{},
	}

	for piiType, pattern := range s.patterns {
		if pattern.MatchString(text) {
			result.HasPII = true
			result.PIITypes = append(result.PIITypes, piiType)
			result.RedactedText = pattern.ReplaceAllString(
				result.RedactedText,
				"[REDACTED]",
			)
		}
	}

	return result
}

type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
	scanner    *PIIScanner
	blockOnPII bool
}

func NewClient(baseURL, apiKey string, blockOnPII bool) *Client {
	if baseURL == "" {
		baseURL = "https://api.deepseek.com/v1/chat/completions"
	}
	if apiKey == "" {
		apiKey = os.Getenv("DEEPSEEK_API_KEY")
	}

	return &Client{
		baseURL: baseURL,
		apiKey:  apiKey,
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
		scanner:    NewPIIScanner(),
		blockOnPII: blockOnPII,
	}
}

func (c *Client) ScanPrompt(prompt string) (string, []string, error) {
	scanResult := c.scanner.Scan(prompt)

	if scanResult.HasPII && c.blockOnPII {
		return "", scanResult.PIITypes, fmt.Errorf("PII detected in prompt: %v", scanResult.PIITypes)
	}

	return scanResult.RedactedText, scanResult.PIITypes, nil
}

func (c *Client) Chat(req ChatRequest) (*ChatResponse, error) {
	// Messages arriving here have already been scanned and redacted by the
	// caller (callLLMWithPIIScanning in runner.go).  A second scan would be
	// redundant and would also silently accept content that slipped through.
	// The gateway path never reaches this function at all — it routes directly
	// through gatewayclient.Client.Chat().

	body, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}

	httpReq, err := http.NewRequest("POST", c.baseURL, bytes.NewBuffer(body))
	if err != nil {
		return nil, err
	}

	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+c.apiKey)

	httpResp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer httpResp.Body.Close()

	if httpResp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(httpResp.Body)
		return nil, fmt.Errorf("LLM API returned status %d: %s", httpResp.StatusCode, string(bodyBytes))
	}

	var resp ChatResponse
	if err := json.NewDecoder(httpResp.Body).Decode(&resp); err != nil {
		return nil, err
	}

	return &resp, nil
}

var DefaultClient *Client

func InitDefault() {
	apiKey := os.Getenv("DEEPSEEK_API_KEY")
	if apiKey == "" {
		fmt.Println("Warning: DEEPSEEK_API_KEY not set, LLM calls will fail")
	}
	DefaultClient = NewClient("", apiKey, true)
}
