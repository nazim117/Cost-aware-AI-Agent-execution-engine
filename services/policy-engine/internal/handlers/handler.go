package handlers

import (
	"encoding/json"
	"engine/internal/policy"
	"engine/internal/types"
	"log"
	"net/http"
)

type PolicyDecisionResponse struct {
	Decision struct {
		Allowed           bool   `json:"allowed"`
		SelectedModelTier string `json:"selected_model_tier"`
		HardStop          bool   `json:"hard_stop"`
	} `json:"decision"`
	Reason        string `json:"reason"`
	PolicyVersion string `json:"policy_version"`
}

func EvaluatePolicyHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var ctx types.AgentStepContext
	if err := json.NewDecoder(r.Body).Decode(&ctx); err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}

	policyCtx := policy.PolicyContext{
		StepName: ctx.Step.Name,
	}

	policyCtx.Budget.Total = ctx.Budget.Total
	policyCtx.Budget.Remaining = ctx.Budget.Remaining

	policyCtx.Request.LatencySLAMs = ctx.Request.LatencySLAMs

	decision := policy.Evaluate(policyCtx)

	log.Println("Received policy evaluation request")
	log.Printf(
		"[POLICY] step=%s budget=%.2f/%.2f sla=%dms",
		ctx.Step.Name,
		ctx.Budget.Remaining,
		ctx.Budget.Total,
		ctx.Request.LatencySLAMs,
	)

	response := PolicyDecisionResponse{
		Decision: struct {
			Allowed           bool   `json:"allowed"`
			SelectedModelTier string `json:"selected_model_tier"`
			HardStop          bool   `json:"hard_stop"`
		}{
			Allowed:           decision.Allowed,
			SelectedModelTier: decision.SelectedModelTier,
			HardStop:          decision.HardStop,
		},
		Reason:        decision.Reason,
		PolicyVersion: "v1.0",
	}

	w.Header().Set("Content-Type", "application/json")
	err := json.NewEncoder(w).Encode(response)
	if err != nil {
		return
	}
}
