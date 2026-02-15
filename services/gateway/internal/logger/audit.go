package logger

import "time"

type AuditLog struct {
	Timestamp   time.Time
	UserID      string
	Prompt      string
	Response    string
	PIIDetected []string
	Model       string
	Cost        float64
}

func LogRequest(log AuditLog) {
	// Write to file or database
	// For MVP: append to JSON file
}
