// Package scanner provides the canonical PII scanning implementation for the
// gateway service.  All PII detection and redaction in the gateway must go
// through this package — do not duplicate regex patterns elsewhere.
package scanner

import (
	"regexp"
)

// patterns is the authoritative set of PII regex patterns used across the
// gateway.  Add new patterns here; the gateway's HTTP handler and any future
// consumers will pick them up automatically.
var patterns = map[string]*regexp.Regexp{
	"ssn":         regexp.MustCompile(`\b\d{3}-\d{2}-\d{4}\b`),
	"email":       regexp.MustCompile(`\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b`),
	"credit_card": regexp.MustCompile(`\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b`),
	"phone":       regexp.MustCompile(`\b\d{3}[-.]?\d{3}[-.]?\d{4}\b`),
}

// ScanResult holds the outcome of a PII scan on a single text string.
type ScanResult struct {
	HasPII       bool     `json:"has_pii"`
	PIITypes     []string `json:"pii_types"`
	RedactedText string   `json:"redacted_text"`
}

// ScanForPII scans text for all known PII patterns.  It returns a ScanResult
// whose RedactedText has every match replaced with "[REDACTED]".  The original
// text is never mutated.
func ScanForPII(text string) ScanResult {
	result := ScanResult{
		RedactedText: text,
		PIITypes:     []string{},
	}

	for piiType, pattern := range patterns {
		if pattern.MatchString(result.RedactedText) {
			result.HasPII = true
			result.PIITypes = append(result.PIITypes, piiType)
			result.RedactedText = pattern.ReplaceAllString(
				result.RedactedText, "[REDACTED]",
			)
		}
	}

	return result
}
