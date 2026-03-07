package agent

import (
	"errors"
	"strings"
	"time"

	"agent-executor/internal/metrics"
	"agent-executor/internal/policyclient"
	"agent-executor/internal/types"
)

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

// baselineTierForStep returns the "ideal" tier for a given step type,
// used to detect and record downgrades in metrics.
func baselineTierForStep(stepType string) string {
	switch stepType {
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

// RunAgent executes an agent run by traversing the step graph.
// If req.StepGraph is nil, DefaultGraph() is used.
// Each step is evaluated by the policy engine before execution.
// The graph traversal follows edge conditions based on live budget state and
// policy decisions, enabling dynamic routing (e.g. budget shortcuts, hard stop bypasses).
func RunAgent(
	req types.AgentRunRequest,
	policy *policyclient.Client,
	m *metrics.Metrics,
) (*types.AgentRunResponse, error) {

	m.IncAgentRun()
	start := time.Now()

	// Resolve the step graph — use caller-supplied graph or fall back to default.
	graph := DefaultGraph()
	if req.StepGraph != nil {
		graph = *req.StepGraph
	}

	remainingBudget := req.Budget
	var totalCost float64
	var trace []types.AgentStepRun

	// Traverse the graph starting at the entry node.
	currentName := graph.Entry
	for currentName != "" {
		node, ok := graph.Nodes[currentName]
		if !ok {
			return nil, errors.New("step graph references unknown node: " + currentName)
		}

		stepType := effectiveStepType(node)

		// Ask the policy engine which model tier to use for this step.
		policyReq := policyclient.PolicyRequest{}
		policyReq.Step.Name = stepType
		policyReq.Budget.Total = req.Budget
		policyReq.Budget.Remaining = remainingBudget
		policyReq.Request.LatencySLAMs = req.LatencySLAMs

		decision, err := policy.Evaluate(policyReq)
		if err != nil {
			return nil, err
		}

		// Compute remaining budget ratio for edge condition evaluation.
		remainingRatio := 0.0
		if req.Budget > 0 {
			remainingRatio = remainingBudget / req.Budget
		}

		// Determine whether this step is executable. If the cheapest possible tier
		// already exceeds the remaining budget, treat it as a hard stop so the graph
		// can route gracefully (e.g. OnHardStop edge to summarize) rather than
		// returning a 500 error.
		cheapestCost := costByTier["cheap"]
		affordabilityStop := costByTier[decision.Decision.SelectedModelTier] > remainingBudget &&
			cheapestCost > remainingBudget

		hardStop := decision.Decision.HardStop || affordabilityStop

		// Resolve the next node before potentially breaking out of the loop.
		// This allows OnHardStop edges to redirect to a graceful exit step
		// rather than halting abruptly.
		currentName = nextStep(node, remainingRatio, hardStop)

		if hardStop {
			m.IncHardStop()
			// If OnHardStop redirected us somewhere (e.g. summarize), continue.
			// Otherwise we're done.
			continue
		}

		// --- Execute the step ---

		baseline := baselineTierForStep(stepType)
		tier := decision.Decision.SelectedModelTier

		// If the selected tier is unaffordable but cheap is not, downgrade to cheap.
		if costByTier[tier] > remainingBudget {
			tier = "cheap"
		}

		if tier != baseline {
			m.IncDowngrade(decision.Reason)
		}

		if strings.Contains(decision.Reason, "sla") {
			m.IncSLAPrevented()
		}

		latency := latencyByTier[tier]
		time.Sleep(latency)

		cost := costByTier[tier]

		remainingBudget -= cost
		totalCost += cost

		m.AddCost(cost)
		m.IncStep(stepType, tier)

		baselineCost := costByTier[baseline]
		if baselineCost > cost {
			m.AddCostSaved(baselineCost - cost)
		}

		trace = append(trace, types.AgentStepRun{
			Step:      node.Name, // use node name in trace for full visibility
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
