# Cost-aware AI Agent Execution Engine

A lightweight control plane for AI agents that enforces **cost**, **latency**, and **execution policies** before agent steps run.

> **Note:** This project is **not** an agent framework. It is an **infrastructure layer** that sits in front of agents and decides:
> - whether an agent step is allowed to run
> - which model tier it may use
> - when execution must stop to protect budget or SLAs

## What problem does this solve?

**AI agents tend to:**
- overspend on expensive models
- ignore latency constraints
- behave unpredictably under load or tight budgets

**This system introduces deterministic, explainable control:**
- budgets are enforced at runtime
- latency SLAs influence model choice
- execution degrades gracefully instead of failing

## Architecture overview

The system consists of **two small services:**

### agent-executor
- orchestrates agent steps
- enforces policy decisions
- simulates cost and latency
- exposes runtime metrics

### policy-engine
- evaluates policies based on:
  - remaining budget
  - step type
  - latency SLA
- returns explicit decisions with reasons

Agents integrate via HTTP — no framework lock-in.

## Features

- ✅ Cost-aware execution (cheap, standard, premium tiers)
- ✅ Latency-aware policy decisions
- ✅ Hard stops when constraints are violated
- ✅ Explainable decisions (reason field)
- ✅ Runtime metrics (`/metrics`)

## Quick Start

### Docker Compose

```bash
docker compose up --build
```

Services:
- `agent-executor` → http://localhost:8081
- `policy-engine` → http://localhost:8080

### Development (Local)

**Agent executor:**
```bash
cd services/agent-executor
go run ./cmd/server
```

**Policy engine:**
```bash
cd services/policy-engine
go run ./cmd/server
```

## API Reference

### Run an agent

**Endpoint:** `POST /agent/run`

**Request:**

```json
{
  "goal": "Analyze customer churn",
  "budget": 0.08,
  "priority": "normal",
  "latency_sla_ms": 200
}
```

**Fields:**
- `goal` — free-form description of the task
- `budget` — total budget available for the agent run
- `priority` — reserved for future policy extensions
- `latency_sla_ms` — maximum acceptable per-step latency

**Response:**

```json
  "result": "simulated agent result",
  "total_cost": 0.045,
  "total_latency_ms": 720,
  "steps": [
    {
      "step": "plan",
      "model_tier": "standard",
      "cost": 0.015,
      "latency_ms": 200,
      "decision": "planning_standard_sla_constrained"
    },
    {
      "step": "execute",
      "model_tier": "cheap",
      "cost": 0.005,
      "latency_ms": 80,
      "decision": "execution_cheap_sla_constrained"
    },
    {
      "step": "summarize",
      "model_tier": "cheap",
      "cost": 0.005,
      "latency_ms": 80,
      "decision": "summarize_forced_cheap"
    }
  ]
}
```

Each step includes: selected model tier, simulated cost and latency, and decision reason from the policy engine.

### Metrics

**Endpoint:** `GET /metrics`

**Example response:**

```json
  "AgentRunsTotal": 3,
  "AgentStepsTotal": {
    "plan": { "standard": 2, "premium": 1 },
    "execute": { "cheap": 3 },
    "summarize": { "cheap": 3 }
  },
  "AgentDowngradesTotal": {
    "planning_standard_sla_constrained": 1
  },
  "AgentHardStopsTotal": 0,
  "AgentCostTotal": 0.13,
  "AgentCostSaved": 0.06,
  "SLAViolationsPrevented": 1
}


These metrics allow you to quantify:

cost savings

policy impact

SLA protection

downgrade frequency

## Integration Guide

### Important: This is NOT an Agent Framework

You do **not** rewrite your agent. Instead, you integrate this system **before model calls**.

### Integration Pattern (Recommended)

1. Your agent decides it wants to run a step
2. It calls `/agent/run` (or later `/policy/evaluate`)
3. The system returns which model tier is allowed
4. Your agent executes using the selected tier

### Example: Python Integration

```python
import requests

resp = requests.post(
    "http://localhost:8081/agent/run",
    json={
        "goal": "Summarize support tickets",
        "budget": 0.05,
        "priority": "normal",
        "latency_sla_ms": 150
    }
)

decision = resp.json()

for step in decision["steps"]:
    tier = step["model_tier"]

    model = {
        "cheap": "gpt-3.5-turbo",
        "standard": "gpt-4o-mini",
        "premium": "gpt-4o"
    }[tier]

    # Call your LLM with the selected model
```

Your system keeps:
- prompts
- tools
- memory
- orchestration logic

**This engine only governs cost and execution constraints.**

## What This System Does NOT Do

- ❌ Call real LLMs
- ❌ Manage prompts
- ❌ Store memory
- ❌ Replace agent frameworks

> This is intentional — it stays focused on infrastructure concerns.

## Repository Layout

```
services/
├── agent-executor/        # Executes agent runs, enforces decisions, exposes metrics
└── policy-engine/         # Evaluates policies based on budget, step, and latency
```

## When Should You Use This?

This system is useful when you:

- 🎯 run agents in production
- 💰 care about predictable costs
- ⏱️ need latency guarantees
- 🔍 want explainable decisions
- 🎮 want infra-level control over AI usage

## Contributing

- 📝 Open issues or pull requests
- 🧩 Keep services small and composable
- 🎯 Prefer explicit logic over implicit behavior

---

**License:** See [LICENSE](LICENSE) for details.