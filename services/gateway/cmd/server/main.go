package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"regexp"
	"time"
)

// OpenAI API types
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

// PII Scanner
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
	HasPII       bool     `json:"has_pii"`
	PIITypes     []string `json:"pii_types"`
	RedactedText string   `json:"redacted_text"`
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

// Audit Logger
type AuditLog struct {
	Timestamp   time.Time `json:"timestamp"`
	UserID      string    `json:"user_id"`
	Model       string    `json:"model"`
	Prompt      string    `json:"prompt"`
	Response    string    `json:"response"`
	PIIDetected []string  `json:"pii_detected"`
	Blocked     bool      `json:"blocked"`
}

func logRequest(log AuditLog) {
	// For MVP: just log to stdout
	// In production: write to database or log aggregator
	logJSON, _ := json.Marshal(log)
	fmt.Printf("AUDIT: %s\n", string(logJSON))
}

// Gateway Server
type Gateway struct {
	scanner    *PIIScanner
	openAIKey  string
	openAIURL  string
	blockOnPII bool
}

func NewGateway() *Gateway {
	apiKey := os.Getenv("DEEPSEEK_API_KEY")
	if apiKey == "" {
		log.Fatal("DEEPSEEK_API_KEY environment variable required")
	}

	return &Gateway{
		scanner:    NewPIIScanner(),
		openAIKey:  apiKey,                                         // Still called openAIKey but holds DeepSeek key
		openAIURL:  "https://api.deepseek.com/v1/chat/completions", // <-- Changed!
		blockOnPII: true,
	}
}

func (g *Gateway) handleChat(w http.ResponseWriter, r *http.Request) {
	// 1. Parse incoming request
	var req ChatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	// 2. Scan all messages for PII
	var allPII []string
	for i, msg := range req.Messages {
		scanResult := g.scanner.Scan(msg.Content)

		if scanResult.HasPII {
			allPII = append(allPII, scanResult.PIITypes...)

			if g.blockOnPII {
				// Block the request
				log.Printf("PII detected, blocking request: %v", scanResult.PIITypes)

				// Log the blocked request
				logRequest(AuditLog{
					Timestamp:   time.Now(),
					Model:       req.Model,
					Prompt:      msg.Content,
					PIIDetected: scanResult.PIITypes,
					Blocked:     true,
				})

				// Return error to client
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusForbidden)
				json.NewEncoder(w).Encode(map[string]interface{}{
					"error": map[string]interface{}{
						"message": fmt.Sprintf("PII detected: %v. Request blocked.", scanResult.PIITypes),
						"type":    "pii_violation",
						"code":    "pii_detected",
					},
				})
				return
			} else {
				// Redact PII and continue
				req.Messages[i].Content = scanResult.RedactedText
			}
		}
	}

	// 3. Forward to OpenAI
	resp, err := g.forwardToOpenAI(req)
	if err != nil {
		log.Printf("Error forwarding to OpenAI: %v", err)
		http.Error(w, "Error contacting OpenAI", http.StatusInternalServerError)
		return
	}

	// 4. Log the request (successful)
	promptText := ""
	if len(req.Messages) > 0 {
		promptText = req.Messages[len(req.Messages)-1].Content
	}
	responseText := ""
	if len(resp.Choices) > 0 {
		responseText = resp.Choices[0].Message.Content
	}

	logRequest(AuditLog{
		Timestamp:   time.Now(),
		Model:       req.Model,
		Prompt:      promptText,
		Response:    responseText,
		PIIDetected: allPII,
		Blocked:     false,
	})

	// 5. Return response to client
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func (g *Gateway) forwardToOpenAI(req ChatRequest) (*ChatResponse, error) {
	// Marshal request to JSON
	body, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}

	// Create HTTP request
	httpReq, err := http.NewRequest("POST", g.openAIURL, bytes.NewBuffer(body))
	if err != nil {
		return nil, err
	}

	// Set headers
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+g.openAIKey)

	// Send request
	client := &http.Client{Timeout: 30 * time.Second}
	httpResp, err := client.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer httpResp.Body.Close()

	// Check status
	if httpResp.StatusCode != http.StatusOK {
		bodyBytes, _ := io.ReadAll(httpResp.Body)
		return nil, fmt.Errorf("OpenAI returned status %d: %s", httpResp.StatusCode, string(bodyBytes))
	}

	// Parse response
	var resp ChatResponse
	if err := json.NewDecoder(httpResp.Body).Decode(&resp); err != nil {
		return nil, err
	}

	return &resp, nil
}

func (g *Gateway) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"status": "healthy",
	})
}

func main() {
	gateway := NewGateway()

	http.HandleFunc("/v1/chat/completions", gateway.handleChat)
	http.HandleFunc("/health", gateway.handleHealth)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8082"
	}

	log.Printf("AI Gateway starting on port %s", port)
	log.Printf("Block on PII: %v", gateway.blockOnPII)
	log.Fatal(http.ListenAndServe(":"+port, nil))
}
