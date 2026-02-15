package scanner

import "regexp"

var patterns = map[string]*regexp.Regexp{
	"ssn":   regexp.MustCompile(`\b\d{3}-\d{2}-\d{4}\b`),
	"email": regexp.MustCompile(`\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b`),
	"ccn":   regexp.MustCompile(`\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b`),
}

type ScanResult struct {
	HasPII       bool
	PIITypes     []string
	RedactedText string
}

func ScanForPII(text string) ScanResult {
	result := ScanResult{RedactedText: text}

	for piiType, pattern := range patterns {
		if pattern.MatchString(text) {
			result.HasPII = true
			result.PIITypes = append(result.PIITypes, piiType)
			result.RedactedText = pattern.ReplaceAllString(
				result.RedactedText, "[REDACTED]",
			)
		}
	}

	return result
}
