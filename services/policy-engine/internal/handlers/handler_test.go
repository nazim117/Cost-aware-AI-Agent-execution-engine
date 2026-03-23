package handlers

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPublicEvaluateHandler(t *testing.T) {
	cases := []struct {
		name           string
		method         string
		body           string
		wantStatus     int
		wantHardStop   *bool
		wantTier       string
		wantReasonPart string
	}{
		{
			name:       "valid execute step with healthy budget",
			method:     http.MethodPost,
			body:       `{"step":"execute","remaining_budget":0.06,"total_budget":0.08,"latency_sla_ms":300,"priority":"normal"}`,
			wantStatus: http.StatusOK,
			wantHardStop: func() *bool { b := false; return &b }(),
			wantTier:   "standard",
		},
		{
			name:       "summarize step always gets cheap tier",
			method:     http.MethodPost,
			body:       `{"step":"summarize","remaining_budget":0.04,"total_budget":0.08,"latency_sla_ms":200,"priority":"normal"}`,
			wantStatus: http.StatusOK,
			wantHardStop: func() *bool { b := false; return &b }(),
			wantTier:   "cheap",
		},
		{
			name:       "exhausted budget returns hard stop",
			method:     http.MethodPost,
			body:       `{"step":"execute","remaining_budget":0.005,"total_budget":0.08,"latency_sla_ms":200,"priority":"normal"}`,
			wantStatus: http.StatusOK,
			wantHardStop: func() *bool { b := true; return &b }(),
		},
		{
			name:       "search step with healthy budget returns standard",
			method:     http.MethodPost,
			body:       `{"step":"search","remaining_budget":0.06,"total_budget":0.08,"latency_sla_ms":300,"priority":"normal"}`,
			wantStatus: http.StatusOK,
			wantHardStop: func() *bool { b := false; return &b }(),
			wantTier:   "standard",
		},
		{
			name:       "validate step always gets cheap tier",
			method:     http.MethodPost,
			body:       `{"step":"validate","remaining_budget":0.06,"total_budget":0.08,"latency_sla_ms":200,"priority":"normal"}`,
			wantStatus: http.StatusOK,
			wantHardStop: func() *bool { b := false; return &b }(),
			wantTier:   "cheap",
		},
		{
			name:       "bad JSON body returns 400",
			method:     http.MethodPost,
			body:       `not-json`,
			wantStatus: http.StatusBadRequest,
		},
		{
			name:       "GET method returns 405",
			method:     http.MethodGet,
			body:       ``,
			wantStatus: http.StatusMethodNotAllowed,
		},
		{
			name:       "zero total budget returns hard stop",
			method:     http.MethodPost,
			body:       `{"step":"execute","remaining_budget":0,"total_budget":0,"latency_sla_ms":200,"priority":"normal"}`,
			wantStatus: http.StatusOK,
			wantHardStop: func() *bool { b := true; return &b }(),
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(tc.method, "/policy/evaluate", bytes.NewBufferString(tc.body))
			req.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()

			PublicEvaluateHandler(w, req)

			if w.Code != tc.wantStatus {
				t.Fatalf("status = %d; want %d (body: %s)", w.Code, tc.wantStatus, w.Body.String())
			}

			if tc.wantStatus != http.StatusOK {
				return
			}

			var resp PolicyDecisionResponse
			if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
				t.Fatalf("decode response: %v", err)
			}

			if tc.wantHardStop != nil && resp.Decision.HardStop != *tc.wantHardStop {
				t.Errorf("HardStop = %v; want %v (reason: %s)", resp.Decision.HardStop, *tc.wantHardStop, resp.Reason)
			}

			if tc.wantTier != "" && resp.Decision.SelectedModelTier != tc.wantTier {
				t.Errorf("SelectedModelTier = %q; want %q (reason: %s)", resp.Decision.SelectedModelTier, tc.wantTier, resp.Reason)
			}

			if resp.PolicyVersion == "" {
				t.Error("PolicyVersion should not be empty")
			}
		})
	}
}
