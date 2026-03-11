package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"time"

	"gateway/internal/logger"
	"gateway/internal/scanner"
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

// Gateway Server
type Gateway struct {
	openAIKey  string
	openAIURL  string
	blockOnPII bool
}

func NewGateway() *Gateway {
	apiKey := os.Getenv("DEEPSEEK_API_KEY")
	if apiKey == "" {
		log.Fatal("DEEPSEEK_API_KEY environment variable required")
	}

	// BLOCK_ON_PII=true: reject requests containing PII with HTTP 403.
	// Default is false: redact PII inline and forward the cleaned request.
	// Use block mode at user-facing API boundaries; use redact mode (default)
	// inside agent pipelines so PII in goals doesn't abort the entire run.
	blockOnPII := os.Getenv("BLOCK_ON_PII") == "true"

	return &Gateway{
		openAIKey:  apiKey,
		openAIURL:  "https://api.deepseek.com/v1/chat/completions",
		blockOnPII: blockOnPII,
	}
}

func (g *Gateway) handleChat(w http.ResponseWriter, r *http.Request) {
	// 1. Parse incoming request
	var req ChatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	// 2. Scan all messages for PII using the canonical scanner package.
	var allPII []string
	for i, msg := range req.Messages {
		scanResult := scanner.ScanForPII(msg.Content)

		if scanResult.HasPII {
			allPII = append(allPII, scanResult.PIITypes...)

			if g.blockOnPII {
				log.Printf("PII detected, blocking request: %v", scanResult.PIITypes)

				logger.LogRequest(logger.AuditLog{
					Timestamp:   time.Now(),
					Model:       req.Model,
					Prompt:      msg.Content,
					PIIDetected: scanResult.PIITypes,
					Blocked:     true,
				})

				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusForbidden)
				json.NewEncoder(w).Encode(map[string]interface{}{
					"error": map[string]interface{}{
						"message":   fmt.Sprintf("PII detected: %v. Request blocked.", scanResult.PIITypes),
						"type":      "pii_violation",
						"code":      "pii_detected",
						"pii_types": scanResult.PIITypes,
					},
				})
				return
			}
			// Redact PII and continue
			req.Messages[i].Content = scanResult.RedactedText
		}
	}

	// 3. Forward to upstream LLM
	resp, err := g.forwardToOpenAI(req)
	if err != nil {
		log.Printf("Error forwarding to LLM: %v", err)
		http.Error(w, "Error contacting upstream LLM", http.StatusInternalServerError)
		return
	}

	// 4. Emit audit log entry for the completed (non-blocked) request.
	promptText := ""
	if len(req.Messages) > 0 {
		promptText = req.Messages[len(req.Messages)-1].Content
	}
	responseText := ""
	if len(resp.Choices) > 0 {
		responseText = resp.Choices[0].Message.Content
	}

	logger.LogRequest(logger.AuditLog{
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
