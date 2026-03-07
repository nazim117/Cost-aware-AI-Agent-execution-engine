package types

// --- Step Graph Types ---

// Condition defines when a graph edge should be followed.
// Conditions are evaluated in order on a node's Edges slice — first match wins.
// If no condition fields are set the edge is never followed; use Always: true as a fallback.
type Condition struct {
	// Follow this edge when remaining/total budget is below this ratio (e.g. 0.30 = 30%).
	BudgetRatioBelow float64 `json:"budget_ratio_below,omitempty"`

	// Follow this edge when the policy engine returned a hard stop for this step.
	OnHardStop bool `json:"on_hard_stop,omitempty"`

	// Unconditional fallback — always follow this edge if no earlier edge matched.
	Always bool `json:"always,omitempty"`
}

// Edge is a directed connection from one StepNode to another.
type Edge struct {
	To        string    `json:"to"`
	Condition Condition `json:"condition,omitempty"`
}

// StepNode is a single node in the step graph.
//
// Name is the unique node identifier used to resolve edges.
// StepType is what gets sent to the policy engine and metrics. If empty, Name is used.
// This lets you have two "search" nodes in the graph (e.g. "search_1", "search_2")
// that both map to the "execute" policy tier.
type StepNode struct {
	Name     string `json:"name"`
	StepType string `json:"step_type,omitempty"`
	Edges    []Edge `json:"edges"` // evaluated in order; first matching edge is followed
}

// StepGraph is a directed graph of agent steps.
// Entry is the name of the first node to execute.
// Nodes is the map of all nodes keyed by their Name.
// A node with no outgoing edges (or no matching edge) is a terminal node.
type StepGraph struct {
	Entry string              `json:"entry"`
	Nodes map[string]StepNode `json:"nodes"`
}

// --- Agent API Types ---

type AgentRunRequest struct {
	Goal         string  `json:"goal"`
	Budget       float64 `json:"budget"`
	Priority     string  `json:"priority"`
	LatencySLAMs int     `json:"latency_sla_ms"`
	// StepGraph overrides the default plan→execute→summarize graph.
	// If nil, DefaultGraph() is used.
	StepGraph *StepGraph `json:"step_graph,omitempty"`
}

type AgentRunResponse struct {
	Result         string         `json:"result"`
	TotalCost      float64        `json:"total_cost"`
	TotalLatencyMs int64          `json:"total_latency_ms"`
	Steps          []AgentStepRun `json:"steps"`
}

type AgentStepRun struct {
	Step      string  `json:"step"`
	ModelTier string  `json:"model_tier"`
	Cost      float64 `json:"cost"`
	LatencyMs int64   `json:"latency_ms"`
	Decision  string  `json:"decision"`
}
