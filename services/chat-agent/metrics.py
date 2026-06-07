# metrics.py — Prometheus custom metrics for LLM cost tracking.
#
# Single responsibility: define the domain-level counters and expose a helper
# that records them after each LLM call.  The standard HTTP metrics
# (request rate, latency histogram, error rate) are handled automatically by
# prometheus-fastapi-instrumentator in main.py — this file only adds the
# domain-specific cost/token counters that fit the "cost-aware" project theme.
#
# Why Counters and not Gauges?
#   Token and cost values only ever increase within a process lifetime.
#   Prometheus Counters are the right type for monotonically increasing values —
#   they survive restarts gracefully and PromQL's rate() function is designed for
#   them.  Use rate(llm_tokens_total[5m]) to see tokens-per-second over 5 minutes.
#
# Why a MODEL_PRICING table here?
#   Pricing is the one piece of data that doesn't come from the LLM response.
#   Keeping it in one dict makes it easy to update when DeepSeek or any other
#   provider changes their rates — no need to touch llm.py or main.py.
#   Unknown models default to (0.0, 0.0) so cost stays 0 rather than crashing.
#
# Metric names follow Prometheus naming conventions:
#   <namespace>_<subsystem>_<unit>_total for counters.
#   The "chat_agent" prefix avoids collisions if other services share a scrape job.

from prometheus_client import Counter, Histogram

# ─── Pricing table ────────────────────────────────────────────────────────────
# (input_usd_per_1k_tokens, output_usd_per_1k_tokens)
# Update this dict whenever provider prices change — it is the single source of
# truth for cost estimation across the whole service.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # DeepSeek (as of 2026-01)
    "deepseek-chat":     (0.00014, 0.00028),
    "deepseek-reasoner": (0.00055, 0.00219),
    # OpenAI (for comparison / future use)
    "gpt-4o":            (0.0025,  0.010),
    "gpt-4o-mini":       (0.00015, 0.0006),
    # Ollama local models: zero cloud cost by definition
    "llama3":            (0.0, 0.0),
    "llama3.2":          (0.0, 0.0),
    "mistral":           (0.0, 0.0),
    "nomic-embed-text":  (0.0, 0.0),  # embedding model, not a chat model
}

# ─── HTTP metrics ─────────────────────────────────────────────────────────────
# These replace prometheus-fastapi-instrumentator (removed to fix a starlette
# version conflict in the Python 3.12 Docker image).  They are incremented by
# MetricsMiddleware in main.py after each request completes.

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "handler", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "handler"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# ─── LLM metrics ──────────────────────────────────────────────────────────────

# Total LLM requests, partitioned by provider, model, and outcome (ok / error).
# Use rate(llm_requests_total[5m]) in Grafana to see requests-per-second.
llm_requests_total = Counter(
    "chat_agent_llm_requests_total",
    "Total LLM completion requests",
    ["provider", "model", "outcome"],
)

# Token throughput — split by kind so you can graph prompt vs completion
# separately (completion tokens cost more on most providers).
# Use rate(chat_agent_llm_tokens_total[5m]) to see tokens-per-second.
llm_tokens_total = Counter(
    "chat_agent_llm_tokens_total",
    "Total tokens sent to / received from the LLM",
    ["provider", "model", "kind"],  # kind: prompt | completion
)

# Cumulative estimated cost in USD.  Not a Gauge — we accumulate so you can
# use increase(chat_agent_llm_cost_usd_total[24h]) for a daily spend figure.
llm_cost_usd_total = Counter(
    "chat_agent_llm_cost_usd_total",
    "Estimated cumulative LLM cost in USD",
    ["provider", "model"],
)


# ─── Helper ───────────────────────────────────────────────────────────────────

def record_llm_usage(
    provider: str,
    model: str,
    usage: dict,
    outcome: str,
) -> None:
    """Increment all LLM metrics after a completion call.

    Args:
        provider: LLM_PROVIDER value, e.g. "openai_compatible" or "ollama".
        model:    Model name, e.g. "deepseek-chat" or "llama3".
        usage:    Dict with keys prompt_tokens, completion_tokens, total_tokens.
                  Comes directly from _chat_ollama / _chat_openai_compatible.
        outcome:  "ok" on success, "error" on HTTPException.
    """
    llm_requests_total.labels(provider=provider, model=model, outcome=outcome).inc()

    if outcome == "ok":
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        llm_tokens_total.labels(provider=provider, model=model, kind="prompt").inc(prompt_tokens)
        llm_tokens_total.labels(provider=provider, model=model, kind="completion").inc(completion_tokens)

        # Look up pricing; unknown models silently default to zero cost.
        input_rate, output_rate = MODEL_PRICING.get(model, (0.0, 0.0))
        cost = (prompt_tokens / 1000.0) * input_rate + (completion_tokens / 1000.0) * output_rate
        llm_cost_usd_total.labels(provider=provider, model=model).inc(cost)
