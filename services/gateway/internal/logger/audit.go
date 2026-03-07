// Package logger provides structured audit logging for the gateway service.
// Every request that passes through the gateway — whether blocked for PII or
// forwarded — is appended as a JSON line to the audit log file.
package logger

import (
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"
)

// AuditLog records a single request event observed by the gateway.
type AuditLog struct {
	Timestamp   time.Time `json:"timestamp"`
	UserID      string    `json:"user_id,omitempty"`
	Model       string    `json:"model"`
	Prompt      string    `json:"prompt"`
	Response    string    `json:"response,omitempty"`
	PIIDetected []string  `json:"pii_detected,omitempty"`
	Blocked     bool      `json:"blocked"`
	Cost        float64   `json:"cost,omitempty"`
}

// auditLogPath is the file to which audit entries are appended.
// Override with the AUDIT_LOG_PATH environment variable.
func auditLogPath() string {
	if p := os.Getenv("AUDIT_LOG_PATH"); p != "" {
		return p
	}
	return "audit.jsonl"
}

var mu sync.Mutex

// LogRequest appends entry to the audit log file as a single JSON line.
// If the file does not exist it is created; if it does exist the entry is
// appended.  Errors are logged to stderr but never returned — a logging
// failure must not abort the request path.
func LogRequest(entry AuditLog) {
	if entry.Timestamp.IsZero() {
		entry.Timestamp = time.Now()
	}

	line, err := json.Marshal(entry)
	if err != nil {
		fmt.Fprintf(os.Stderr, "audit logger: marshal error: %v\n", err)
		return
	}

	mu.Lock()
	defer mu.Unlock()

	f, err := os.OpenFile(auditLogPath(), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		fmt.Fprintf(os.Stderr, "audit logger: open file: %v\n", err)
		return
	}
	defer f.Close()

	if _, err := fmt.Fprintf(f, "%s\n", line); err != nil {
		fmt.Fprintf(os.Stderr, "audit logger: write error: %v\n", err)
	}
}
