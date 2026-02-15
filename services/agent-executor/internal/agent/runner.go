package agent

import (
	"agent-executor/internal/policyclient"
	"agent-executor/internal/types"
	"errors"
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

func Run(
	req types.AgentRunRequest,
	policy *policyclient.Client,
) (*types.AgentRunResponse, error) {

	remainingBudget := req.Budget
	var totalCost float64
	var trace []types.AgentStepRun

	for _, step := range steps {

		policyReq := policyclient.PolicyRequest{}
		policyReq.Step.Name = step
		policyReq.Budget.Total = req.Budget
		policyReq.Budget.Remaining = remainingBudget

		decision, err := policy.Evaluate(policyReq)
		if err != nil {
			return nil, err
		}

		if decision.Decision.HardStop {
			break
		}

		tier := decision.Decision.SelectedModelTier
		cost := costByTier[tier]

		if cost > remainingBudget {
			return nil, errors.New("budget exceeded")
		}

		remainingBudget -= cost
		totalCost += cost

		trace = append(trace, types.AgentStepRun{
			Step:      step,
			ModelTier: tier,
			Cost:      cost,
			Decision:  decision.Reason,
		})
	}

	return &types.AgentRunResponse{
		Result:    "simulated agent result",
		TotalCost: totalCost,
		Steps:     trace,
	}, nil
}
