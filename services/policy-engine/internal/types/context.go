package types

type AgentStepContext struct {
	AgentID string `json:"agent_id"`

	Step    StepContext `json:"step"`
	Budget  Budget      `json:"budget"`
	Request Request     `json:"request"`
	System  System      `json:"system"`
	History History     `json:"history"`
}

type StepContext struct {
	Name       string `json:"name"`
	Index      int    `json:"index"`
	TotalSteps int    `json:"total_steps"`
}

type Budget struct {
	Total      float64 `json:"total"`
	Remaining  float64 `json:"remaining"`
	SpentSoFar float64 `json:"spent_so_far"`
}

type Request struct {
	Priority     string `json:"priority"`
	LatencySLAMs int    `json:"latency_sla_ms"`
}

type System struct {
	CurrentLoad  float64 `json:"current_load"`
	ActiveAgents int     `json:"active_agents"`
}

type History struct {
	PreviousSteps []PreviousStep `json:"previous_steps"`
}

type PreviousStep struct {
	Step      string  `json:"step"`
	ModelTier string  `json:"model_tier"`
	Cost      float64 `json:"cost"`
	Decision  string  `json:"decision"`
}
