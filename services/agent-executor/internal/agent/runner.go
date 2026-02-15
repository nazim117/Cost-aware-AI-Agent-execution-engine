package agent

import (
	"errors"
	"strings"
	"time"

	"agent-executor/internal/metrics"
	"agent-executor/internal/policyclient"
	"agent-executor/internal/types"
)

var steps = []string{
	"plan",
	"execute",
	"summarize",
}

var costByTier = map[string]float64{
	"cheap":    0.005,
	"standard": 0.015,
	"premium":  0.030,
}

var latencyByTier = map[string]time.Duration{
	"cheap":    80 * time.Millisecond,
	"standard": 200 * time.Millisecond,
	"premium":  450 * time.Millisecond,
}

func baselineTierForStep(step string) string {
	switch step {
	case "plan":
		return "premium"
	case "execute":
		return "standard"
	case "summarize":
		return "cheap"
	default:
		return "standard"
	}
}

func RunAgent(
	req types.AgentRunRequest,
	policy *policyclient.Client,
	metrics *metrics.Metrics,
) (*types.AgentRunResponse, error) {

	metrics.IncAgentRun()
	start := time.Now()

	remainingBudget := req.Budget
	var totalCost float64
	var trace []types.AgentStepRun

	for _, step := range steps {

		policyReq := policyclient.PolicyRequest{}
		policyReq.Step.Name = step
		policyReq.Budget.Total = req.Budget
		policyReq.Budget.Remaining = remainingBudget
		policyReq.Request.LatencySLAMs = req.LatencySLAMs

		decision, err := policy.Evaluate(policyReq)
		if err != nil {
			return nil, err
		}

		if decision.Decision.HardStop {
			metrics.IncHardStop()
			break
		}

		baseline := baselineTierForStep(step)
		tier := decision.Decision.SelectedModelTier

		// Metrics: downgrade detection
		if tier != baseline {
			metrics.IncDowngrade(decision.Reason)
		}

		// Metrics: SLA protection
		if strings.Contains(decision.Reason, "sla") {
			metrics.IncSLAPrevented()
		}

		latency := latencyByTier[tier]
		time.Sleep(latency)

		cost := costByTier[tier]
		if cost > remainingBudget {
			return nil, errors.New("budget exceeded")
		}

		remainingBudget -= cost
		totalCost += cost

		metrics.AddCost(cost)
		metrics.IncStep(step, tier)

		// Cost saved vs baseline
		baselineCost := costByTier[baseline]
		if baselineCost > cost {
			metrics.AddCostSaved(baselineCost - cost)
		}

		trace = append(trace, types.AgentStepRun{
			Step:      step,
			ModelTier: tier,
			Cost:      cost,
			LatencyMs: latency.Milliseconds(),
			Decision:  decision.Reason,
		})
	}

	totalLatency := time.Since(start)

	return &types.AgentRunResponse{
		Result:         "simulated agent result",
		TotalCost:      totalCost,
		TotalLatencyMs: totalLatency.Milliseconds(),
		Steps:          trace,
	}, nil
}
