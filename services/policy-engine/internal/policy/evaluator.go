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

func Evaluate(ctx BudgetContext) Decision {
	remainingRatio := ctx.Budget.Remaining / ctx.Budget.Total

	// Hard stop if budget critically low
	if remainingRatio < 0.10 {
		return Decision{
			Allowed:           false,
			SelectedModelTier: "",
			HardStop:          true,
			Reason:            "budget_exhausted",
		}
	}

	// Downgrade if budget is low
	if remainingRatio < 0.40 {
		return Decision{
			Allowed:           true,
			SelectedModelTier: "cheap",
			HardStop:          false,
			Reason:            "budget_protection",
		}
	}

	// Default behavior
	return Decision{
		Allowed:           true,
		SelectedModelTier: "standard",
		HardStop:          false,
		Reason:            "default_allow",
	}
}
