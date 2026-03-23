package runs

import (
	"crypto/rand"
	"fmt"
	"sync"
)

const maxEntries = 100

// StepEntry records the policy decision and cost for a single step within a run.
type StepEntry struct {
	Step      string  `json:"step"`
	ModelTier string  `json:"model_tier"`
	Cost      float64 `json:"cost"`
	LatencyMs int64   `json:"latency_ms"`
	Decision  string  `json:"decision"`
}

// RunEntry is a single agent run record stored in the ring buffer.
type RunEntry struct {
	RunID          string      `json:"run_id"`
	Goal           string      `json:"goal"`
	Timestamp      string      `json:"timestamp"`
	TotalCost      float64     `json:"total_cost"`
	TotalLatencyMs int64       `json:"total_latency_ms"`
	Steps          []StepEntry `json:"steps"`
}

// Buffer is a bounded in-memory ring buffer of the last maxEntries runs.
// All methods are safe for concurrent use.
type Buffer struct {
	mu      sync.Mutex
	entries []RunEntry
}

// NewBuffer returns an empty Buffer.
func NewBuffer() *Buffer {
	return &Buffer{}
}

// NewRunID generates a random UUID v4-style identifier using crypto/rand.
func NewRunID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

// Add prepends e to the buffer, evicting the oldest entry when over capacity.
func (buf *Buffer) Add(e RunEntry) {
	buf.mu.Lock()
	defer buf.mu.Unlock()
	buf.entries = append([]RunEntry{e}, buf.entries...)
	if len(buf.entries) > maxEntries {
		buf.entries = buf.entries[:maxEntries]
	}
}

// All returns a copy of all entries, newest first.
func (buf *Buffer) All() []RunEntry {
	buf.mu.Lock()
	defer buf.mu.Unlock()
	result := make([]RunEntry, len(buf.entries))
	copy(result, buf.entries)
	return result
}

// Get returns the entry with the given run_id, or nil if not found.
func (buf *Buffer) Get(id string) *RunEntry {
	buf.mu.Lock()
	defer buf.mu.Unlock()
	for i := range buf.entries {
		if buf.entries[i].RunID == id {
			e := buf.entries[i]
			return &e
		}
	}
	return nil
}
