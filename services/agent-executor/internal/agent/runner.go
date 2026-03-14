package agent

import (
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"strings"
	"time"

	"agent-executor/internal/gatewayclient"
	"agent-executor/internal/llmclient"
	"agent-executor/internal/mcpclient"
	"agent-executor/internal/metrics"
	"agent-executor/internal/policyclient"
	"agent-executor/internal/types"
)

const maxToolResultChars = 8000 // Limit tool result to avoid hitting LLM context limits

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
	var result string
	var accumulatedContext string

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

		log.Printf("[runner] Step=%s, Budget.Total=%v, Remaining=%v", stepType, req.Budget, remainingBudget)

		decision, err := policy.Evaluate(policyReq)
		if err != nil {
			return nil, err
		}

		log.Printf("[runner] Decision: SelectedTier=%s, HardStop=%v, Reason=%s",
			decision.Decision.SelectedModelTier, decision.Decision.HardStop, decision.Reason)

		// Determine whether this step is executable. If the cheapest possible tier
		// already exceeds the remaining budget, treat it as a hard stop so the graph
		// can route gracefully (e.g. OnHardStop edge to summarize) rather than
		// returning a 500 error.
		cheapestCost := costByTier["cheap"]
		affordabilityStop := costByTier[decision.Decision.SelectedModelTier] > remainingBudget &&
			cheapestCost > remainingBudget

		log.Printf("[runner] cheapestCost=%v, affordabilityStop=%v", cheapestCost, affordabilityStop)

		hardStop := decision.Decision.HardStop || affordabilityStop

		if hardStop {
			m.IncHardStop()
			// Emit a trace record with an empty ModelTier so the frontend can
			// render the node in a "stopped" state rather than leaving it grey
			// as if it were never visited.
			trace = append(trace, types.AgentStepRun{
				Step:      node.Name,
				ModelTier: "",
				Cost:      0,
				LatencyMs: 0,
				Decision:  decision.Reason,
				Content:   "",
			})
			remainingRatio := 0.0
			if req.Budget > 0 {
				remainingRatio = remainingBudget / req.Budget
			}
			currentName = nextStep(node, remainingRatio, true)
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

		// Then in the step execution block, after getting content back:
		content, toolCalls, err := callLLMWithPIIScanning(req.Goal, stepType, tier, node.Name, accumulatedContext)
		if err != nil {
			return nil, err
		}
		if content != "" {
			if accumulatedContext != "" {
				accumulatedContext += "\n\n---\n\n"
			}
			accumulatedContext += fmt.Sprintf("[%s step]:\n%s", node.Name, content)
		}

		actualLatency := time.Since(startTime)
		if actualLatency < latency {
			time.Sleep(latency - actualLatency)
		}
		// recordedLatency is the true wall-clock time for this step,
		// always >= the simulated tier floor after the conditional sleep above.
		recordedLatency := time.Since(startTime)

		cost := costByTier[tier]

		remainingBudget -= cost
		totalCost += cost

		m.AddCost(cost)
		m.IncStep(stepType, tier)

		baselineCost := costByTier[baseline]
		if baselineCost > cost {
			m.AddCostSaved(baselineCost - cost)
		}

		// Evaluate the next node AFTER deducting this step's cost so that
		// budget_ratio_below edge conditions see the up-to-date remaining ratio.
		remainingRatio := 0.0
		if req.Budget > 0 {
			remainingRatio = remainingBudget / req.Budget
		}
		currentName = nextStep(node, remainingRatio, false)

		trace = append(trace, types.AgentStepRun{
			Step:      node.Name,
			ModelTier: tier,
			Cost:      cost,
			LatencyMs: recordedLatency.Milliseconds(),
			Decision:  decision.Reason,
			Content:   content,
			ToolCalls: toolCalls,
		})
		result = content
	}

	totalLatency := time.Since(start)

	return &types.AgentRunResponse{
		Result:         result,
		TotalCost:      totalCost,
		TotalLatencyMs: totalLatency.Milliseconds(),
		Steps:          trace,
	}, nil
}

// callLLMWithPIIScanning is the single choke-point through which every LLM
// call in the agent-executor must pass.  It enforces PII hygiene before any
// text reaches the model and drives the multi-turn tool-call loop when MCP
// tools are available.
//
// Returns the final text content, the list of MCP tool calls made (if any),
// and any error.
//
//  1. Gateway path (preferred, when GATEWAY_URL is set):
//     The full chat request is sent to the gateway service, which performs its
//     own PII scan, blocks or redacts as configured, emits an audit log entry,
//     and forwards the cleaned request to the upstream LLM.  When MCP tools are
//     available (MCP_SERVER_URL is set), the tools array is included in the
//     request and the response is checked for tool_calls.  Tool calls are
//     dispatched to the MCP server and their results appended as "tool" messages
//     before re-calling the LLM, up to maxToolIter times.
//
//  2. Local fallback (when GATEWAY_URL is not set):
//     The llmclient's built-in PIIScanner runs first.  Tool dispatch is not
//     supported in this path.
func callLLMWithPIIScanning(goal, stepType, tier, stepName, priorContext string) (string, []types.ToolCallTrace, error) {
	// Ensure clients are initialised (all are no-ops after the first call).
	gatewayclient.InitDefault()
	mcpclient.InitDefault()
	if llmclient.DefaultClient == nil {
		llmclient.InitDefault()
	}

	systemPrompt := getSystemPromptForStep(stepType)
	model := modelByTier[tier]

	// ── Gateway path ─────────────────────────────────────────────────────────
	if gatewayclient.Available() {
		userMsg := goal
		if priorContext != "" {
			userMsg = "Original goal: " + goal + "\n\nFindings from previous steps:\n" + priorContext
		}
		msgs := []gatewayclient.Message{
			{Role: "system", Content: systemPrompt},
			{Role: "user", Content: userMsg},
		}

		// Fetch MCP tool definitions and convert to the OpenAI tools format.
		var tools []gatewayclient.Tool
		if mcpclient.Available() && stepType == "execute" {
			if mcpTools, err := mcpclient.DefaultClient.ListTools(); err == nil {
				for _, t := range mcpTools {
					props := make(map[string]gatewayclient.Property, len(t.InputSchema.Properties))
					for k, v := range t.InputSchema.Properties {
						props[k] = gatewayclient.Property{
							Type:        v.Type,
							Description: v.Description,
						}
					}
					tools = append(tools, gatewayclient.Tool{
						Type: "function",
						Function: gatewayclient.ToolFunction{
							Name:        t.Name,
							Description: t.Description,
							Parameters: gatewayclient.JSONSchema{
								Type:       t.InputSchema.Type,
								Properties: props,
								Required:   t.InputSchema.Required,
							},
						},
					})
				}
			}
		}

		var allToolCalls []types.ToolCallTrace
		const maxToolIter = 5

		for iter := 0; iter < maxToolIter; iter++ {
			chatReq := gatewayclient.ChatRequest{
				Model:       model,
				Messages:    msgs,
				Temperature: 0.7,
				MaxTokens:   1024,
			}
			if len(tools) > 0 {
				chatReq.Tools = tools
				chatReq.ToolChoice = "auto"
			}

			resp, err := gatewayclient.DefaultClient.Chat(chatReq)
			if err != nil {
				return "", allToolCalls, err
			}
			if len(resp.Choices) == 0 {
				return "", allToolCalls, nil
			}

			choice := resp.Choices[0]

			log.Printf("[runner] iter=%d, finishReason=%q, toolCallsCount=%d", iter, choice.FinishReason, len(choice.Message.ToolCalls))

			// No tool calls — conversation complete.
			if choice.FinishReason != "tool_calls" || len(choice.Message.ToolCalls) == 0 {
				log.Printf("[runner] No tool calls in response, returning content directly")
				return choice.Message.Content, allToolCalls, nil
			}

			// Append the assistant's tool-call message to the conversation.
			msgs = append(msgs, choice.Message)

			// Dispatch each requested tool call to the MCP server.
			for _, tc := range choice.Message.ToolCalls {
				var args map[string]any
				_ = json.Unmarshal([]byte(tc.Function.Arguments), &args)

				log.Printf("[runner] iter=%d, tool=%s, args=%v", iter, tc.Function.Name, args)

				tcStart := time.Now()
				mcpResult, callErr := mcpclient.DefaultClient.CallTool(tc.Function.Name, args)
				tcLatency := time.Since(tcStart)

				resultText := ""
				isError := false
				if callErr != nil {
					resultText = callErr.Error()
					isError = true
				} else if len(mcpResult.Content) > 0 {
					resultText = mcpResult.Content[0].Text
					isError = mcpResult.IsError
				}

				// Truncate result if too long to avoid hitting LLM context limits
				if len(resultText) > maxToolResultChars {
					resultText = resultText[:maxToolResultChars] + "\n[...truncated for length...]"
					log.Printf("[runner] iter=%d, toolResult TRUNCATED from %d to %d chars", iter, len(mcpResult.Content[0].Text), maxToolResultChars)
				}

				log.Printf("[runner] iter=%d, toolResultLength=%d, isError=%v", iter, len(resultText), isError)

				allToolCalls = append(allToolCalls, types.ToolCallTrace{
					ToolName:  tc.Function.Name,
					Arguments: args,
					Result:    resultText,
					IsError:   isError,
					LatencyMs: tcLatency.Milliseconds(),
				})

				// Append the tool result so the LLM can continue.
				msgs = append(msgs, gatewayclient.Message{
					Role:       "tool",
					Content:    resultText,
					ToolCallID: tc.ID,
					Name:       tc.Function.Name,
				})
			}
		}

		log.Printf("[runner] max tool iterations (%d) reached for step %q, forcing final synthesis", maxToolIter, stepName)
		msgs = append(msgs, gatewayclient.Message{
			Role:    "user",
			Content: "Based on all the tool results above, provide a clear and direct answer to the original question. Do not call any more tools.",
		})
		finalReq := gatewayclient.ChatRequest{
			Model:       model,
			Messages:    msgs,
			Temperature: 0.3, // lower temp for factual synthesis
			MaxTokens:   512,
		}
		finalResp, err := gatewayclient.DefaultClient.Chat(finalReq)
		if err != nil || len(finalResp.Choices) == 0 {
			// fallback to the last assistant message if synthesis call fails
			for i := len(msgs) - 1; i >= 0; i-- {
				if msgs[i].Role == "assistant" && msgs[i].Content != "" {
					return msgs[i].Content, allToolCalls, nil
				}
			}
			return "", allToolCalls, nil
		}
		return finalResp.Choices[0].Message.Content, allToolCalls, nil
	}

	// ── Local fallback path (no tool dispatch) ────────────────────────────────
	// Run the local PII scanner.  Use the *redacted* text so that raw PII is
	// never forwarded to the LLM even when blockOnPII is false.
	redactedGoal, piiTypes, err := llmclient.DefaultClient.ScanPrompt(goal)
	if err != nil {
		return "", nil, err
	}

	userContent := redactedGoal
	if len(piiTypes) > 0 {
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
		return "", nil, err
	}

	if len(resp.Choices) > 0 {
		return resp.Choices[0].Message.Content, nil, nil
	}
	return "", nil, nil
}

func getSystemPromptForStep(stepType string) string {
	switch stepType {
	case "plan":
		return "You are a planning agent. You MUST use the available tools to gather real information — never answer from memory. For weather: call http_request with url=https://wttr.in/{city}?format=j1. Replace {city} with the actual city name using + for spaces (e.g. New+York)."
	case "execute":
		return `You are an execution agent with access to tools. You MUST use tools to answer — never answer from memory or training data.
Rules:
- ALWAYS call a tool first. Never respond with text before calling at least one tool.
- For weather: call http_request with url="https://wttr.in/{city}?format=j1" (e.g. https://wttr.in/London?format=j1)
- For web research: call web_search first, then web_fetch on the most relevant result.
- After receiving tool results, provide a clear answer based on the actual data returned.
- Do NOT call more tools after you have the data you need.`
	case "synthesize":
		return "You are a synthesis agent. Combine all tool results and information gathered in previous steps into a coherent, detailed response. Do NOT call any tools."
	case "summarize":
		return "You are a summarization agent. Summarize the findings concisely. Do NOT call any tools — just synthesize what you already have."
	default:
		return "You are a helpful assistant. Do NOT make tool calls unless absolutely necessary. After getting information, provide your answer immediately."
	}
}
