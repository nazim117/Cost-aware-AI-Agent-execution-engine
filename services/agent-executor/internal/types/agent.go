package types

type AgentRunRequest struct {
	Goal     string  `json:"goal"`
	Budget   float64 `json:"budget"`
	Priority string  `json:"priority"`
}

type AgentRunResponse struct {
	Result    string         `json:"result"`
	TotalCost float64        `json:"total_cost"`
	Steps     []AgentStepRun `json:"steps"`
}

type AgentStepRun struct {
	Step      string  `json:"step"`
	ModelTier string  `json:"model_tier"`
	Cost      float64 `json:"cost"`
	Decision  string  `json:"decision"`
}
