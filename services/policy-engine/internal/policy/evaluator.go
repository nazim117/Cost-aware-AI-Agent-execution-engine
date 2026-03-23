package policy

type Decision struct {
	Allowed           bool
	SelectedModelTier string
	HardStop          bool
	Reason            string
}

type BudgetContext struct {
	Budget struct {
		Total     float64
		Remaining float64
	}
}

type PolicyContext struct {
	StepName string

	Budget struct {
		Total     float64
		Remaining float64
	}

	Request struct {
		LatencySLAMs int
	}
}

func Evaluate(ctx PolicyContext) Decision {
	// Guard: zero total budget is always a hard stop.
	if ctx.Budget.Total <= 0 {
		return Decision{
			Allowed:           false,
			SelectedModelTier: "",
			HardStop:          true,
			Reason:            "budget_exhausted",
		}
	}

	remainingRatio := ctx.Budget.Remaining / ctx.Budget.Total

	// --- HARD STOP: budget exhaustion ---
	if remainingRatio < 0.10 {
		return Decision{
			Allowed:           false,
			SelectedModelTier: "",
			HardStop:          true,
			Reason:            "budget_exhausted",
		}
	}

	// --- STEP-SPECIFIC OVERRIDES ---
	switch ctx.StepName {

	case "summarize":
		// Summarization is always low value
		return Decision{
			Allowed:           true,
			SelectedModelTier: "cheap",
			HardStop:          false,
			Reason:            "summarize_forced_cheap",
		}

	case "plan":
		// Planning can use premium if budget allows and latency SLA permits
		if ctx.Request.LatencySLAMs >= 450 && remainingRatio >= 0.40 {
			return Decision{
				Allowed:           true,
				SelectedModelTier: "premium",
				HardStop:          false,
				Reason:            "planning_premium_allowed",
			}
		}
		// Try standard if latency SLA permits
		if ctx.Request.LatencySLAMs >= 200 && remainingRatio >= 0.25 {
			return Decision{
				Allowed:           true,
				SelectedModelTier: "standard",
				HardStop:          false,
				Reason:            "planning_standard_sla_constrained",
			}
		}

	case "execute":
		// Execution defaults to standard if budget and latency SLA allow
		if ctx.Request.LatencySLAMs >= 200 && remainingRatio >= 0.40 {
			return Decision{
				Allowed:           true,
				SelectedModelTier: "standard",
				HardStop:          false,
				Reason:            "execution_standard",
			}
		}
		// Fall back to cheap if latency permits
		if ctx.Request.LatencySLAMs >= 80 && remainingRatio >= 0.15 {
			return Decision{
				Allowed:           true,
				SelectedModelTier: "cheap",
				HardStop:          false,
				Reason:            "execution_cheap_sla_constrained",
			}
		}

	case "search":
		// Search defaults to standard; falls back to cheap on tight budget or SLA
		if ctx.Request.LatencySLAMs >= 200 && remainingRatio >= 0.30 {
			return Decision{
				Allowed:           true,
				SelectedModelTier: "standard",
				HardStop:          false,
				Reason:            "search_standard",
			}
		}
		if ctx.Request.LatencySLAMs >= 80 && remainingRatio >= 0.15 {
			return Decision{
				Allowed:           true,
				SelectedModelTier: "cheap",
				HardStop:          false,
				Reason:            "search_cheap_sla_constrained",
			}
		}

	case "validate":
		// Validation is lightweight — always use cheap tier
		return Decision{
			Allowed:           true,
			SelectedModelTier: "cheap",
			HardStop:          false,
			Reason:            "validate_cheap",
		}
	}

	// --- BUDGET PROTECTION FALLBACK ---
	if remainingRatio < 0.40 {
		// Still respect latency SLA even in budget protection mode
		if ctx.Request.LatencySLAMs >= 200 {
			return Decision{
				Allowed:           true,
				SelectedModelTier: "standard",
				HardStop:          false,
				Reason:            "budget_protection_standard",
			}
		}
		if ctx.Request.LatencySLAMs >= 80 {
			return Decision{
				Allowed:           true,
				SelectedModelTier: "cheap",
				HardStop:          false,
				Reason:            "budget_protection_cheap",
			}
		}
		// If SLA is too strict, hard stop
		return Decision{
			Allowed:           false,
			SelectedModelTier: "",
			HardStop:          true,
			Reason:            "latency_sla_unachievable",
		}
	}

	// --- DEFAULT ---
	return Decision{
		Allowed:           true,
		SelectedModelTier: "standard",
		HardStop:          false,
		Reason:            "default_allow",
	}
}
