package agent

import "agent-executor/internal/types"

// Matches reports whether this condition is satisfied given the current execution state.
// Conditions are evaluated in the order they appear on an Edge slice — first match wins.
func matchesCondition(c types.Condition, remainingRatio float64, hardStop bool) bool {
	if c.OnHardStop && hardStop {
		return true
	}
	if c.BudgetRatioBelow > 0 && remainingRatio < c.BudgetRatioBelow {
		return true
	}
	if c.Always {
		return true
	}
	return false
}

// nextStep returns the name of the next node to visit given the current execution state,
// or an empty string if no edge condition matches (terminal node).
func nextStep(node types.StepNode, remainingRatio float64, hardStop bool) string {
	for _, edge := range node.Edges {
		if matchesCondition(edge.Condition, remainingRatio, hardStop) {
			return edge.To
		}
	}
	return ""
}

// effectiveStepType returns the step name to use for policy evaluation and metrics.
// If the node defines a StepType override, that is used; otherwise the node Name is used.
func effectiveStepType(node types.StepNode) string {
	if node.StepType != "" {
		return node.StepType
	}
	return node.Name
}

// --- Built-in Graphs ---

// DefaultGraph returns the classic linear plan → execute → summarize graph.
// This is used when no StepGraph is provided in the AgentRunRequest.
func DefaultGraph() types.StepGraph {
	return types.StepGraph{
		Entry: "plan",
		Nodes: map[string]types.StepNode{
			"plan": {
				Name: "plan",
				Edges: []types.Edge{
					{To: "execute", Condition: types.Condition{Always: true}},
				},
			},
			"execute": {
				Name: "execute",
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{Always: true}},
				},
			},
			"summarize": {
				Name:  "summarize",
				Edges: []types.Edge{}, // terminal
			},
		},
	}
}

// ResearchGraph returns a multi-step research graph:
//
//	plan → search → deep_search → synthesize → summarize
//
// Budget-aware shortcuts:
//   - plan skips straight to summarize if budget ratio drops below 30%
//   - search skips deep_search and goes straight to synthesize below 25%
func ResearchGraph() types.StepGraph {
	return types.StepGraph{
		Entry: "plan",
		Nodes: map[string]types.StepNode{
			"plan": {
				Name: "plan",
				Edges: []types.Edge{
					// Hard-stop fallback: jump to summarize on budget exhaustion
					{To: "summarize", Condition: types.Condition{OnHardStop: true}},
					// Budget too tight for full research — skip straight to summarize
					{To: "summarize", Condition: types.Condition{BudgetRatioBelow: 0.30}},
					// Normal path
					{To: "search", Condition: types.Condition{Always: true}},
				},
			},
			"search": {
				Name:     "search",
				StepType: "execute", // policy engine treats this as an execute step
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{OnHardStop: true}},
					// Budget tight — skip deep search
					{To: "synthesize", Condition: types.Condition{BudgetRatioBelow: 0.25}},
					{To: "deep_search", Condition: types.Condition{Always: true}},
				},
			},
			"deep_search": {
				Name:     "deep_search",
				StepType: "execute",
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{OnHardStop: true}},
					{To: "synthesize", Condition: types.Condition{Always: true}},
				},
			},
			"synthesize": {
				Name: "synthesize",
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{Always: true}},
				},
			},
			"summarize": {
				Name:  "summarize",
				Edges: []types.Edge{}, // terminal
			},
		},
	}
}

// CodeReviewGraph returns a step graph suited for code review tasks:
//
//	plan → read_code → analyse → draft_review → summarize
//
// Falls back to cheap analysis path when budget is constrained.
func CodeReviewGraph() types.StepGraph {
	return types.StepGraph{
		Entry: "plan",
		Nodes: map[string]types.StepNode{
			"plan": {
				Name: "plan",
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{OnHardStop: true}},
					{To: "read_code", Condition: types.Condition{Always: true}},
				},
			},
			"read_code": {
				Name:     "read_code",
				StepType: "execute",
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{OnHardStop: true}},
					// Tight budget — skip deep analysis
					{To: "draft_review", Condition: types.Condition{BudgetRatioBelow: 0.30}},
					{To: "analyse", Condition: types.Condition{Always: true}},
				},
			},
			"analyse": {
				Name:     "analyse",
				StepType: "execute",
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{OnHardStop: true}},
					{To: "draft_review", Condition: types.Condition{Always: true}},
				},
			},
			"draft_review": {
				Name: "draft_review",
				Edges: []types.Edge{
					{To: "summarize", Condition: types.Condition{Always: true}},
				},
			},
			"summarize": {
				Name:  "summarize",
				Edges: []types.Edge{},
			},
		},
	}
}
