package agent

import (
	"testing"

	"agent-executor/internal/types"
)

// --- matchesCondition tests ---

func TestMatchesCondition(t *testing.T) {
	cases := []struct {
		name        string
		condition   types.Condition
		remainRatio float64
		hardStop    bool
		expectMatch bool
	}{
		{
			name:        "always matches unconditionally",
			condition:   types.Condition{Always: true},
			remainRatio: 0.5,
			hardStop:    false,
			expectMatch: true,
		},
		{
			name:        "budget_ratio_below matches when under threshold",
			condition:   types.Condition{BudgetRatioBelow: 0.30},
			remainRatio: 0.20,
			hardStop:    false,
			expectMatch: true,
		},
		{
			name:        "budget_ratio_below does not match when above threshold",
			condition:   types.Condition{BudgetRatioBelow: 0.30},
			remainRatio: 0.50,
			hardStop:    false,
			expectMatch: false,
		},
		{
			name:        "budget_ratio_below does not match at exact threshold",
			condition:   types.Condition{BudgetRatioBelow: 0.30},
			remainRatio: 0.30,
			hardStop:    false,
			expectMatch: false,
		},
		{
			name:        "on_hard_stop matches when hard stop is true",
			condition:   types.Condition{OnHardStop: true},
			remainRatio: 0.05,
			hardStop:    true,
			expectMatch: true,
		},
		{
			name:        "on_hard_stop does not match when hard stop is false",
			condition:   types.Condition{OnHardStop: true},
			remainRatio: 0.50,
			hardStop:    false,
			expectMatch: false,
		},
		{
			name:        "empty condition never matches",
			condition:   types.Condition{},
			remainRatio: 0.50,
			hardStop:    false,
			expectMatch: false,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := matchesCondition(tc.condition, tc.remainRatio, tc.hardStop)
			if got != tc.expectMatch {
				t.Errorf("matchesCondition(%+v, ratio=%.2f, hardStop=%v) = %v; want %v",
					tc.condition, tc.remainRatio, tc.hardStop, got, tc.expectMatch)
			}
		})
	}
}

// --- nextStep tests ---

func TestNextStep(t *testing.T) {
	cases := []struct {
		name       string
		node       types.StepNode
		ratio      float64
		hardStop   bool
		expectNext string
	}{
		{
			name: "terminal node returns empty string",
			node: types.StepNode{
				Name:  "summarize",
				Edges: []types.Edge{},
			},
			ratio:      0.5,
			hardStop:   false,
			expectNext: "",
		},
		{
			name: "always edge is followed",
			node: types.StepNode{
				Name: "plan",
				Edges: []types.Edge{
					{To: "execute", Condition: types.Condition{Always: true}},
				},
			},
			ratio:      0.8,
			hardStop:   false,
			expectNext: "execute",
		},
		{
			name: "budget shortcut takes priority over always edge",
			node: types.StepNode{
				Name: "plan",
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{BudgetRatioBelow: 0.30}},
					{To: "execute", Condition: types.Condition{Always: true}},
				},
			},
			ratio:      0.20,
			hardStop:   false,
			expectNext: "summarize",
		},
		{
			name: "hard stop edge fires before budget edge",
			node: types.StepNode{
				Name: "search",
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{OnHardStop: true}},
					{To: "summarize", Condition: types.Condition{BudgetRatioBelow: 0.30}},
					{To: "deep_search", Condition: types.Condition{Always: true}},
				},
			},
			ratio:      0.05,
			hardStop:   true,
			expectNext: "summarize",
		},
		{
			name: "normal path taken when budget is healthy",
			node: types.StepNode{
				Name: "search",
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{OnHardStop: true}},
					{To: "synthesize", Condition: types.Condition{BudgetRatioBelow: 0.25}},
					{To: "deep_search", Condition: types.Condition{Always: true}},
				},
			},
			ratio:      0.60,
			hardStop:   false,
			expectNext: "deep_search",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := nextStep(tc.node, tc.ratio, tc.hardStop)
			if got != tc.expectNext {
				t.Errorf("nextStep() = %q; want %q", got, tc.expectNext)
			}
		})
	}
}

// --- effectiveStepType tests ---

func TestEffectiveStepType(t *testing.T) {
	cases := []struct {
		name     string
		node     types.StepNode
		expected string
	}{
		{
			name:     "uses StepType when set",
			node:     types.StepNode{Name: "search_1", StepType: "execute"},
			expected: "execute",
		},
		{
			name:     "falls back to Name when StepType is empty",
			node:     types.StepNode{Name: "plan"},
			expected: "plan",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := effectiveStepType(tc.node)
			if got != tc.expected {
				t.Errorf("effectiveStepType() = %q; want %q", got, tc.expected)
			}
		})
	}
}

// --- DefaultGraph sanity tests ---

func TestDefaultGraph(t *testing.T) {
	g := DefaultGraph()

	if g.Entry != "plan" {
		t.Errorf("DefaultGraph().Entry = %q; want %q", g.Entry, "plan")
	}

	expectedNodes := []string{"plan", "execute", "summarize"}
	for _, n := range expectedNodes {
		if _, ok := g.Nodes[n]; !ok {
			t.Errorf("DefaultGraph() missing node %q", n)
		}
	}

	// Verify linear traversal: plan → execute → summarize → ""
	traversal := traverseNames(g)
	expected := []string{"plan", "execute", "summarize"}
	if !sliceEqual(traversal, expected) {
		t.Errorf("DefaultGraph traversal = %v; want %v", traversal, expected)
	}
}

// --- ResearchGraph sanity tests ---

func TestResearchGraphNormalPath(t *testing.T) {
	g := ResearchGraph()

	// Normal budget — should go plan → search → deep_search → synthesize → summarize
	traversal := traverseNamesWithState(g, 1.0, false)
	expected := []string{"plan", "search", "deep_search", "synthesize", "summarize"}
	if !sliceEqual(traversal, expected) {
		t.Errorf("ResearchGraph normal path = %v; want %v", traversal, expected)
	}
}

func TestResearchGraphBudgetShortcut(t *testing.T) {
	g := ResearchGraph()

	// Budget below 25% at search — should skip deep_search
	// We simulate: plan is visited at 0.60 ratio (healthy), then ratio drops
	// We test the shortcut by traversing with low ratio from the start.
	// With ratio=0.20 at plan: BudgetRatioBelow 0.30 fires → jumps to summarize.
	traversal := traverseNamesWithState(g, 0.20, false)
	expected := []string{"plan", "summarize"}
	if !sliceEqual(traversal, expected) {
		t.Errorf("ResearchGraph budget shortcut = %v; want %v", traversal, expected)
	}
}

// --- helpers ---

// traverseNames simulates graph traversal with healthy budget (no shortcuts).
func traverseNames(g types.StepGraph) []string {
	return traverseNamesWithState(g, 1.0, false)
}

// traverseNamesWithState simulates graph traversal with fixed budget ratio and hard stop state.
func traverseNamesWithState(g types.StepGraph, ratio float64, hardStop bool) []string {
	var names []string
	current := g.Entry
	visited := map[string]int{}
	for current != "" {
		visited[current]++
		if visited[current] > 20 {
			// Guard against infinite loops in tests
			break
		}
		names = append(names, current)
		node, ok := g.Nodes[current]
		if !ok {
			break
		}
		current = nextStep(node, ratio, hardStop)
	}
	return names
}

func sliceEqual(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
