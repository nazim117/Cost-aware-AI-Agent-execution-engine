# test_metrics.py — unit tests for metrics.py
#
# Verifies that record_llm_usage correctly increments Prometheus counters
# and computes cost from MODEL_PRICING.  Uses table-style parametrize per
# CLAUDE.md conventions.  No network or LLM calls needed.

import pytest
from prometheus_client import REGISTRY

import metrics as m


def _read_counter(name: str, **labels) -> float:
    """Read the current value of a Prometheus counter by sample name + label set.

    prometheus_client stores Counter("foo_total", ...) with a metric family name
    of "foo" but emits samples named "foo_total".  Matching on sample.name (not
    metric.name) is therefore the reliable approach regardless of client version.
    We also skip "_created" samples (timestamp, not count) by checking the name
    suffix.
    """
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if (
                sample.name == name
                and not sample.name.endswith("_created")
                and all(sample.labels.get(k) == v for k, v in labels.items())
            ):
                return sample.value
    return 0.0


# ─── record_llm_usage — happy path ───────────────────────────────────────────

@pytest.mark.parametrize("provider,model,usage,expected_cost", [
    (
        "openai_compatible",
        "deepseek-chat",
        {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
        # (1000/1000 * 0.00014) + (500/1000 * 0.00028) = 0.00014 + 0.00014 = 0.00028
        0.00028,
    ),
    (
        "ollama",
        "llama3",
        {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
        # llama3 is local — zero cost
        0.0,
    ),
    (
        "openai_compatible",
        "unknown-model-xyz",
        {"prompt_tokens": 500, "completion_tokens": 500, "total_tokens": 1000},
        # Unknown model defaults to (0.0, 0.0) — no crash, cost stays 0
        0.0,
    ),
])
def test_record_llm_usage_ok(provider, model, usage, expected_cost):
    # Capture counter values before the call so the test is idempotent even
    # if other tests have already incremented the same label set.
    before_requests = _read_counter("chat_agent_llm_requests_total",
                                    provider=provider, model=model, outcome="ok")
    before_prompt = _read_counter("chat_agent_llm_tokens_total",
                                  provider=provider, model=model, kind="prompt")
    before_completion = _read_counter("chat_agent_llm_tokens_total",
                                      provider=provider, model=model, kind="completion")
    before_cost = _read_counter("chat_agent_llm_cost_usd_total",
                                provider=provider, model=model)

    m.record_llm_usage(provider, model, usage, "ok")

    assert _read_counter("chat_agent_llm_requests_total",
                         provider=provider, model=model, outcome="ok") == before_requests + 1

    assert _read_counter("chat_agent_llm_tokens_total",
                         provider=provider, model=model, kind="prompt") == before_prompt + usage["prompt_tokens"]

    assert _read_counter("chat_agent_llm_tokens_total",
                         provider=provider, model=model, kind="completion") == before_completion + usage["completion_tokens"]

    actual_cost = _read_counter("chat_agent_llm_cost_usd_total",
                                provider=provider, model=model)
    assert abs((actual_cost - before_cost) - expected_cost) < 1e-9, (
        f"Expected cost delta {expected_cost}, got {actual_cost - before_cost}"
    )


# ─── record_llm_usage — error path ────────────────────────────────────────────

def test_record_llm_usage_error_increments_requests_only():
    """On error, only the requests counter increments — no tokens or cost recorded."""
    provider, model = "openai_compatible", "deepseek-chat"

    before_requests = _read_counter("chat_agent_llm_requests_total",
                                    provider=provider, model=model, outcome="error")
    before_prompt = _read_counter("chat_agent_llm_tokens_total",
                                  provider=provider, model=model, kind="prompt")
    before_cost = _read_counter("chat_agent_llm_cost_usd_total",
                                provider=provider, model=model)

    m.record_llm_usage(provider, model, {}, "error")

    assert _read_counter("chat_agent_llm_requests_total",
                         provider=provider, model=model, outcome="error") == before_requests + 1
    # Tokens and cost must NOT change on error.
    assert _read_counter("chat_agent_llm_tokens_total",
                         provider=provider, model=model, kind="prompt") == before_prompt
    assert _read_counter("chat_agent_llm_cost_usd_total",
                         provider=provider, model=model) == before_cost


# ─── MODEL_PRICING sanity ─────────────────────────────────────────────────────

def test_model_pricing_deepseek_chat_present():
    assert "deepseek-chat" in m.MODEL_PRICING
    input_rate, output_rate = m.MODEL_PRICING["deepseek-chat"]
    assert input_rate > 0
    assert output_rate > 0


def test_unknown_model_defaults_to_zero():
    assert m.MODEL_PRICING.get("nonexistent-model-abc", (0.0, 0.0)) == (0.0, 0.0)
