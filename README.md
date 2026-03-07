# Cost-aware AI Agent Execution Engine

A lightweight control plane for AI agents that enforces **cost**, **latency**, **execution policies**, and **PII safety** before agent steps run.

> **Note:** This project is **not** an agent framework. It is an **infrastructure layer** that sits in front of agents and decides:
> - whether an agent step is allowed to run
> - which model tier it may use
> - when execution must stop to protect budget or SLAs
> - whether a prompt contains PII that must be redacted or blocked before reaching the LLM

## What problem does this solve?

**AI agents tend to:**
- overspend on expensive models
- ignore latency constraints
- behave unpredictably under load or tight budgets
- leak personally identifiable information (PII) into LLM calls — especially critical for research and data agents whose goals naturally contain emails, phone numbers, and other sensitive identifiers

**This system introduces deterministic, explainable control:**
- budgets are enforced at runtime
- latency SLAs influence model choice
- execution degrades gracefully instead of failing
- PII is scanned, redacted or blocked before any text reaches the model
- every LLM call is audit-logged with PII detection results

## Architecture

The system consists of **three services:**

```
┌─────────────┐     POST /agent/run      ┌───────────────────┐
│   Client    │ ───────────────────────► │  agent-executor   │
└─────────────┘                          │     :8081         │
                                         └────────┬──────────┘
                                                  │
                          ┌───────────────────────┼──────────────────────┐
                          │                       │                      │
                          ▼                       ▼                      │
              ┌───────────────────┐   ┌───────────────────┐             │
              │  policy-engine    │   │     gateway        │             │
              │     :8080         │   │     :8082          │             │
              │                   │   │                    │             │
              │  Evaluates step   │   │  Scans PII         │             │
              │  against budget   │   │  Redacts/blocks    │             │
              │  and latency SLA  │   │  Forwards to LLM   │◄────────────┘
              │  Returns model    │   │  Audit logs every  │
              │  tier decision    │   │  request           │
              └───────────────────┘   └────────┬───────────┘
                                               │
                                               ▼
                                    ┌───────────────────┐
                                    │   DeepSeek API    │
                                    │  (or any OpenAI-  │
                                    │  compatible LLM)  │
                                    └───────────────────┘
```

### agent-executor `:8081`
- Orchestrates agent steps by traversing a configurable step graph
- Asks the policy engine which model tier to use for each step
- Routes every LLM call through the gateway (PII enforcement is mandatory, not optional)
- Exposes runtime metrics

### policy-engine `:8080`
- Evaluates each step against remaining budget, step type, and latency SLA
- Returns an explicit decision: allowed tier, hard stop flag, and a reason string
- Enables graceful degradation (e.g. downgrade to `cheap`, route to summarize on hard stop)

### gateway `:8082`
- Scans every message for PII (SSN, email, credit card, phone) before forwarding
- Two modes, controlled by `BLOCK_ON_PII`:
  - **Redact** (default): replaces PII with `[REDACTED]` and continues
  - **Block**: rejects the request with HTTP 403
- Forwards cleaned requests to the upstream LLM (DeepSeek by default)
- Appends a structured JSON audit log entry for every request (blocked or forwarded)
- Also usable as a standalone OpenAI-compatible proxy

## Features

- Cost-aware execution (cheap / standard / premium model tiers)
- Latency-aware policy decisions
- Hard stops when constraints are violated
- Explainable decisions (reason field on every step)
- Runtime metrics (`/metrics`)
- PII scanning on every LLM call — SSN, email, credit card, phone
- Configurable redact-or-block PII enforcement
- Structured audit log (JSONL) with PII detection results per request
- Configurable step graph — define arbitrary agent workflows per request

## Environment Variables

| Service | Variable | Default | Description |
|---|---|---|---|
| gateway | `DEEPSEEK_API_KEY` | — | **Required.** API key for the upstream LLM |
| gateway | `BLOCK_ON_PII` | `false` | `true` to reject requests containing PII; `false` to redact and continue |
| gateway | `AUDIT_LOG_PATH` | `audit.jsonl` | Path to the JSONL audit log file |
| gateway | `PORT` | `8082` | Listening port |
| agent-executor | `DEEPSEEK_API_KEY` | — | Used by the local PII scanner fallback when no gateway is reachable |
| agent-executor | `GATEWAY_URL` | `http://localhost:8082` | Gateway base URL |
| agent-executor | `POLICY_ENGINE_URL` | `http://localhost:8080` | Policy engine base URL |
| agent-executor | `PORT` | `8081` | Listening port |
| policy-engine | `PORT` | `8080` | Listening port |

## Quick Start

### Prerequisites

- Go 1.21+
- A DeepSeek API key (or any OpenAI-compatible API key)

### Docker Compose

```bash
export DEEPSEEK_API_KEY=your_key_here
docker compose up --build
```

Services:
- `agent-executor` → http://localhost:8081
- `policy-engine`  → http://localhost:8080
- `gateway`        → http://localhost:8082

To enable PII blocking (reject instead of redact):

```bash
DEEPSEEK_API_KEY=your_key BLOCK_ON_PII=true docker compose up --build
```

### Local Development

Start all three services. The gateway must be up before the agent-executor receives requests.

```bash
# Terminal 1 — policy engine
cd services/policy-engine
go run ./cmd/server
```

```bash
# Terminal 2 — gateway
cd services/gateway
DEEPSEEK_API_KEY=your_key go run ./cmd/server
```

```bash
# Terminal 3 — agent-executor
cd services/agent-executor
DEEPSEEK_API_KEY=your_key go run ./cmd/server
```

> Run all commands from their respective service directories. To interact with the API, use the repository root as your working directory for `curl` or client code.

## API Reference

### POST /agent/run

Run an agent against a step graph. The engine evaluates each step, enforces budget and latency constraints, scans for PII, and calls the LLM.

**Request:**

```json
{
  "goal": "Analyze customer churn for the EMEA region",
  "budget": 0.10,
  "priority": "normal",
  "latency_sla_ms": 300,
  "step_graph": {
    "entry": "plan",
    "nodes": {
      "plan": {
        "name": "plan",
        "edges": [
          { "to": "execute", "condition": { "always": true } }
        ]
      },
      "execute": {
        "name": "execute",
        "step_type": "execute",
        "edges": [
          { "to": "summarize", "condition": { "budget_ratio_below": 0.3 } },
          { "to": "execute",   "condition": { "always": true } }
        ]
      },
      "summarize": {
        "name": "summarize",
        "edges": []
      }
    }
  }
}
```

**Fields:**

| Field | Type | Required | Description |
|---|---|---|---|
| `goal` | string | yes | Free-form task description. Scanned for PII before reaching the LLM. |
| `budget` | float | yes | Total budget for the entire run in USD |
| `latency_sla_ms` | int | yes | Maximum acceptable per-step latency in milliseconds |
| `priority` | string | no | Reserved for future policy extensions |
| `step_graph` | object | no | Custom step graph. Omit to use the built-in default (plan → execute → summarize) |

**Step graph fields:**

| Field | Description |
|---|---|
| `entry` | Name of the first node to execute |
| `nodes` | Map of node name → node definition |
| `step_type` | `plan`, `execute`, or `summarize`. Determines baseline model tier and system prompt. Defaults to node name if omitted. |
| `edges[].to` | Name of the next node |
| `edges[].condition.always` | Unconditional transition |
| `edges[].condition.budget_ratio_below` | Transition when `remaining/total` drops below this value |
| `edges[].condition.on_hard_stop` | Transition when policy engine issues a hard stop |

**Successful response:**

```json
{
  "result": "simulated agent result",
  "total_cost": 0.05,
  "total_latency_ms": 1340,
  "steps": [
    {
      "step": "plan",
      "model_tier": "premium",
      "cost": 0.030,
      "latency_ms": 450,
      "decision": "planning_premium_allowed"
    },
    {
      "step": "execute",
      "model_tier": "standard",
      "cost": 0.015,
      "latency_ms": 200,
      "decision": "execution_standard_allowed"
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

**PII blocked response** (when `BLOCK_ON_PII=true` and goal contains PII):

```
HTTP 422 Unprocessable Entity
```

```json
{
  "error": {
    "type": "pii_violation",
    "message": "Request blocked: PII detected in goal",
    "pii_types": ["email", "phone"]
  }
}
```

---

### GET /metrics

Returns runtime counters for the agent-executor.

```json
{
  "AgentRunsTotal": 5,
  "AgentStepsTotal": {
    "plan":      { "premium": 4, "standard": 1 },
    "execute":   { "standard": 3, "cheap": 2 },
    "summarize": { "cheap": 5 }
  },
  "AgentDowngradesTotal": {
    "planning_standard_sla_constrained": 1
  },
  "AgentHardStopsTotal": 1,
  "AgentCostTotal": 0.195,
  "AgentCostSaved": 0.075,
  "SLAViolationsPrevented": 2
}
```

---

### POST /v1/chat/completions (gateway)

The gateway also works as a standalone OpenAI-compatible proxy with PII enforcement. Use it as a drop-in replacement for any OpenAI client by pointing `base_url` at `http://localhost:8082`.

**Request:** standard OpenAI chat completions format.

```json
{
  "model": "deepseek-chat",
  "messages": [
    { "role": "user", "content": "Send report to jane.smith@corp.com" }
  ],
  "max_tokens": 512
}
```

**Redact mode** (`BLOCK_ON_PII=false`, default): PII is replaced before forwarding. The LLM receives `"Send report to [REDACTED]"`. The full audit entry is written to `audit.jsonl`.

**Block mode** (`BLOCK_ON_PII=true`):

```
HTTP 403 Forbidden
```

```json
{
  "error": {
    "type": "pii_violation",
    "code": "pii_detected",
    "message": "PII detected: [email]. Request blocked.",
    "pii_types": ["email"]
  }
}
```

---

### GET /health

Available on all three services. Returns `{"status": "healthy"}`.

---

## Audit Log

The gateway writes one JSON line to `audit.jsonl` (or `$AUDIT_LOG_PATH`) for every request:

```json
{
  "timestamp": "2026-03-08T00:42:28Z",
  "model": "deepseek-coder",
  "prompt": "Send report to [REDACTED] and call +44 7911 123456",
  "response": "Here is a plan for sending the report...",
  "pii_detected": ["email"],
  "blocked": false
}
```

The `prompt` field always contains the **redacted** text — raw PII is never written to the log.

---

## PII Detection

The following patterns are detected and redacted/blocked:

| Type | Example |
|---|---|
| Email | `jane.smith@corp.com` → `[REDACTED]` |
| SSN | `123-45-6789` → `[REDACTED]` |
| Credit card | `4111 1111 1111 1111` → `[REDACTED]` |
| Phone | `555-867-5309` → `[REDACTED]` |

> Phone detection uses a 10-digit US format regex. UK numbers (`+44 7911 123456`) are not currently matched — add patterns in `services/gateway/internal/scanner/pii.go`.

---

## Model Tiers

| Tier | Model | Cost (simulated) | Latency (simulated) | Default for |
|---|---|---|---|---|
| `cheap` | `deepseek-chat` | $0.005 | 80ms | summarize |
| `standard` | `deepseek-chat` | $0.015 | 200ms | execute |
| `premium` | `deepseek-coder` | $0.030 | 450ms | plan |

The policy engine may downgrade a step to a lower tier when budget or latency constraints are tight. Downgrades are recorded in metrics.

---

## Integration Guide

### This is not an agent framework

You do **not** rewrite your agent. You call `/agent/run` to have the engine orchestrate steps, or call `/policy/evaluate` directly to get a tier decision and execute the LLM call yourself.

### Example: Python integration

```python
import requests

resp = requests.post(
    "http://localhost:8081/agent/run",
    json={
        "goal": "Summarize support tickets for Q1",
        "budget": 0.05,
        "latency_sla_ms": 150,
    }
)

result = resp.json()

# Check for PII block
if resp.status_code == 422:
    print("Blocked:", result["error"]["pii_types"])
else:
    for step in result["steps"]:
        tier = step["model_tier"]
        model = {
            "cheap":    "deepseek-chat",
            "standard": "deepseek-chat",
            "premium":  "deepseek-coder",
        }[tier]
        # use model for your own LLM call
```

### Example: use the gateway as a standalone PII proxy

```python
from openai import OpenAI

client = OpenAI(
    api_key="your_deepseek_key",
    base_url="http://localhost:8082/v1",  # point at gateway
)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "Email john@example.com the Q1 report"}],
)
# PII is redacted before the request reaches DeepSeek
```

Your agent keeps its own prompts, tools, memory, and orchestration logic. This engine governs cost, latency, and data safety.

---

## Repository Layout

```
services/
├── agent-executor/          # Step orchestration, policy enforcement, gateway routing
│   ├── cmd/server/          # HTTP server entry point
│   └── internal/
│       ├── agent/           # Runner, step graph
│       ├── gatewayclient/   # HTTP client for the PII gateway
│       ├── handlers/        # HTTP handlers
│       ├── llmclient/       # Direct LLM client (fallback when no gateway)
│       ├── metrics/         # Runtime counters
│       ├── policyclient/    # HTTP client for the policy engine
│       └── types/           # Shared request/response types
│
├── gateway/                 # PII scanning proxy
│   ├── cmd/server/          # HTTP server entry point
│   └── internal/
│       ├── logger/          # Structured audit log (JSONL)
│       └── scanner/         # PII regex patterns (canonical, single source of truth)
│
└── policy-engine/           # Budget and latency policy evaluation
    ├── cmd/server/          # HTTP server entry point
    └── internal/
        ├── handlers/        # HTTP handlers
        ├── policy/          # Evaluator
        └── types/           # Policy context types
```

---

## What This System Does NOT Do

- Manage prompts or memory
- Replace agent frameworks (LangChain, CrewAI, etc.)
- Provide a UI or dashboard
- Persist agent state between runs

> This is intentional — it stays focused on infrastructure concerns: cost, latency, and data safety.

---

## Contributing

- Open issues or pull requests
- Keep services small and composable
- Prefer explicit logic over implicit behavior
- New PII patterns go in `services/gateway/internal/scanner/pii.go` — this is the single source of truth used by both the gateway proxy and the agent pipeline
