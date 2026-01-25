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

	policyCtx := policy.BudgetContext{
		Budget: struct {
			Total     float64
			Remaining float64
		}{
			Total:     ctx.Budget.Total,
			Remaining: ctx.Budget.Remaining,
		},
	}

	decision := policy.Evaluate(policyCtx)

	log.Println("Received policy evaluation request")
	
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
