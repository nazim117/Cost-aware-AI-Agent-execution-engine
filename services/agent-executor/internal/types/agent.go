package types

type AgentRunRequest struct {
	Goal         string  `json:"goal"`
	Budget       float64 `json:"budget"`
	Priority     string  `json:"priority"`
	LatencySLAMs int     `json:"latency_sla_ms"`
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
