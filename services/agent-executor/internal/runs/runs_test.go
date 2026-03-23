package runs

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// --- Buffer unit tests ---

func TestBufferCapAt100(t *testing.T) {
	buf := NewBuffer()
	for i := 0; i < 110; i++ {
		buf.Add(RunEntry{RunID: fmt.Sprintf("id-%d", i), Goal: "test"})
	}
	all := buf.All()
	if len(all) != 100 {
		t.Errorf("expected 100 entries after adding 110, got %d", len(all))
	}
}

func TestBufferNewestFirst(t *testing.T) {
	buf := NewBuffer()
	buf.Add(RunEntry{RunID: "first", Goal: "a"})
	buf.Add(RunEntry{RunID: "second", Goal: "b"})
	buf.Add(RunEntry{RunID: "third", Goal: "c"})

	all := buf.All()
	if len(all) != 3 {
		t.Fatalf("expected 3 entries, got %d", len(all))
	}
	if all[0].RunID != "third" {
		t.Errorf("expected newest entry first, got %q", all[0].RunID)
	}
	if all[2].RunID != "first" {
		t.Errorf("expected oldest entry last, got %q", all[2].RunID)
	}
}

func TestBufferGetExisting(t *testing.T) {
	buf := NewBuffer()
	buf.Add(RunEntry{RunID: "abc-123", Goal: "find me"})

	entry := buf.Get("abc-123")
	if entry == nil {
		t.Fatal("expected to find entry, got nil")
	}
	if entry.Goal != "find me" {
		t.Errorf("goal = %q; want %q", entry.Goal, "find me")
	}
}

func TestBufferGetMissing(t *testing.T) {
	buf := NewBuffer()
	buf.Add(RunEntry{RunID: "exists", Goal: "test"})

	entry := buf.Get("does-not-exist")
	if entry != nil {
		t.Errorf("expected nil for missing id, got %+v", entry)
	}
}

func TestBufferGetReturnsIsolatedCopy(t *testing.T) {
	buf := NewBuffer()
	buf.Add(RunEntry{RunID: "copy-test", Goal: "original"})

	entry := buf.Get("copy-test")
	entry.Goal = "mutated"

	// Original should be unchanged.
	original := buf.Get("copy-test")
	if original.Goal != "original" {
		t.Errorf("buffer entry mutated via returned pointer; got %q", original.Goal)
	}
}

func TestNewRunIDUniqueness(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 50; i++ {
		id := NewRunID()
		if seen[id] {
			t.Fatalf("duplicate run ID generated: %s", id)
		}
		seen[id] = true
	}
}

// --- HTTP handler tests (inline to avoid import cycle) ---

// runsHandlerFunc is a minimal version of RunsHandler for test use.
func runsHandlerFunc(buf *Buffer) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		all := buf.All()
		if all == nil {
			all = []RunEntry{}
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(all)
	}
}

// runHandlerFunc is a minimal version of RunHandler for test use.
func runHandlerFunc(buf *Buffer) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		id := strings.TrimPrefix(r.URL.Path, "/runs/")
		if id == "" {
			http.Error(w, "missing run id", http.StatusBadRequest)
			return
		}
		entry := buf.Get(id)
		if entry == nil {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(entry)
	}
}

func TestRunsHandlerEmpty(t *testing.T) {
	buf := NewBuffer()
	req := httptest.NewRequest(http.MethodGet, "/runs", nil)
	w := httptest.NewRecorder()

	runsHandlerFunc(buf)(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; want 200", w.Code)
	}
	var result []RunEntry
	if err := json.NewDecoder(w.Body).Decode(&result); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(result) != 0 {
		t.Errorf("expected empty array, got %d entries", len(result))
	}
}

func TestRunsHandlerPopulated(t *testing.T) {
	buf := NewBuffer()
	buf.Add(RunEntry{RunID: "r1", Goal: "first", TotalCost: 0.01})
	buf.Add(RunEntry{RunID: "r2", Goal: "second", TotalCost: 0.02})

	req := httptest.NewRequest(http.MethodGet, "/runs", nil)
	w := httptest.NewRecorder()

	runsHandlerFunc(buf)(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; want 200", w.Code)
	}
	var result []RunEntry
	if err := json.NewDecoder(w.Body).Decode(&result); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(result) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(result))
	}
	if result[0].RunID != "r2" {
		t.Errorf("expected newest first, got %q", result[0].RunID)
	}
}

func TestRunHandlerFound(t *testing.T) {
	buf := NewBuffer()
	buf.Add(RunEntry{RunID: "xyz", Goal: "lookup me", TotalCost: 0.05})

	req := httptest.NewRequest(http.MethodGet, "/runs/xyz", nil)
	req.URL.Path = "/runs/xyz"
	w := httptest.NewRecorder()

	runHandlerFunc(buf)(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d; want 200 (body: %s)", w.Code, w.Body.String())
	}
	var entry RunEntry
	if err := json.NewDecoder(w.Body).Decode(&entry); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if entry.RunID != "xyz" {
		t.Errorf("run_id = %q; want %q", entry.RunID, "xyz")
	}
}

func TestRunHandlerNotFound(t *testing.T) {
	buf := NewBuffer()

	req := httptest.NewRequest(http.MethodGet, "/runs/missing", nil)
	req.URL.Path = "/runs/missing"
	w := httptest.NewRecorder()

	runHandlerFunc(buf)(w, req)

	if w.Code != http.StatusNotFound {
		t.Fatalf("status = %d; want 404", w.Code)
	}
}
