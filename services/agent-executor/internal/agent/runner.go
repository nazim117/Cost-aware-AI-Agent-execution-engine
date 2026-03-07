package agent

import (
	"errors"
	"strings"
	"time"

	"agent-executor/internal/gatewayclient"
	"agent-executor/internal/llmclient"
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

var modelByTier = map[string]string{
	"cheap":    "deepseek-chat",
	"standard": "deepseek-chat",
	"premium":  "deepseek-coder",
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
		startTime := time.Now()

		_, err = callLLMWithPIIScanning(req.Goal, stepType, tier, node.Name)
		actualLatency := time.Since(startTime)

		if err != nil {
			return nil, err
		}

		if actualLatency < latency {
			time.Sleep(latency - actualLatency)
		}

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

// callLLMWithPIIScanning is the single choke-point through which every LLM
// call in the agent-executor must pass.  It enforces PII hygiene before any
// text reaches the model:
//
//  1. Gateway path (preferred, when GATEWAY_URL is set):
//     The full chat request is sent to the gateway service, which performs its
//     own PII scan, blocks or redacts as configured, emits an audit log entry,
//     and forwards the cleaned request to the upstream LLM.  The agent-executor
//     never touches the LLM directly in this path.
//
//  2. Local fallback (when GATEWAY_URL is not set):
//     The llmclient's built-in PIIScanner runs first.  The redacted text (not
//     the original goal) is used in the chat request.  blockOnPII=true means
//     any PII in the goal causes an immediate error rather than a silent pass.
func callLLMWithPIIScanning(goal, stepType, tier, stepName string) (string, error) {
	// Ensure clients are initialised.  Both are no-ops after the first call.
	gatewayclient.InitDefault()
	if llmclient.DefaultClient == nil {
		llmclient.InitDefault()
	}

	systemPrompt := getSystemPromptForStep(stepType)
	model := modelByTier[tier]

	// --- Gateway path ---
	// When a gateway is available, hand off the entire request.  The gateway
	// is the authoritative PII enforcement point; we do not run a second local
	// scan on top of it.
	if gatewayclient.Available() {
		req := gatewayclient.ChatRequest{
			Model: model,
			Messages: []gatewayclient.Message{
				{Role: "system", Content: systemPrompt},
				{Role: "user", Content: goal},
			},
			Temperature: 0.7,
			MaxTokens:   1024,
		}
		resp, err := gatewayclient.DefaultClient.Chat(req)
		if err != nil {
			return "", err
		}
		if len(resp.Choices) > 0 {
			return resp.Choices[0].Message.Content, nil
		}
		return "", nil
	}

	// --- Local fallback path ---
	// Run the local PII scanner.  Use the *redacted* text in the request so
	// that raw PII is never forwarded to the LLM even if blockOnPII is false.
	redactedGoal, piiTypes, err := llmclient.DefaultClient.ScanPrompt(goal)
	if err != nil {
		// ScanPrompt returns an error only when PII is found and blockOnPII=true.
		return "", err
	}

	userContent := redactedGoal
	if len(piiTypes) > 0 {
		// Prefix lets downstream consumers know redaction occurred without
		// leaking what was redacted.
		userContent = "[PII REDACTED] " + redactedGoal
	}

	req := llmclient.ChatRequest{
		Model: model,
		Messages: []llmclient.Message{
			{Role: "system", Content: systemPrompt},
			{Role: "user", Content: userContent},
		},
		Temperature: 0.7,
		MaxTokens:   1024,
	}

	resp, err := llmclient.DefaultClient.Chat(req)
	if err != nil {
		return "", err
	}

	if len(resp.Choices) > 0 {
		return resp.Choices[0].Message.Content, nil
	}

	return "", nil
}

func getSystemPromptForStep(stepType string) string {
	switch stepType {
	case "plan":
		return "You are a planning agent. Analyze the user's request and create a detailed plan with steps."
	case "execute":
		return "You are an execution agent. Carry out the given plan and provide results."
	case "summarize":
		return "You are a summarization agent. Summarize the results concisely."
	default:
		return "You are a helpful assistant."
	}
}
