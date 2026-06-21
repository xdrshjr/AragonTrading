"""usage 跨 provider 归一单测（RN-2）：两 provider 字段 → 对外 camelCase，绝不外泄 snake_case。"""
from __future__ import annotations

from app.agent.llm.retry import normalize_usage


def test_anthropic_usage_normalized():
    assert normalize_usage("anthropic", {"input_tokens": 10, "output_tokens": 20}) == \
        {"inputTokens": 10, "outputTokens": 20}


def test_openai_usage_normalized():
    assert normalize_usage("openai", {"prompt_tokens": 5, "completion_tokens": 7}) == \
        {"inputTokens": 5, "outputTokens": 7}


def test_missing_or_empty_falls_back_to_none():
    assert normalize_usage("anthropic", None) is None
    assert normalize_usage("anthropic", {}) is None
    assert normalize_usage("openai", {"unrelated": 1}) is None


def test_partial_usage_keeps_present_key():
    # Anthropic message_delta 常仅含 output_tokens；input 缺失回退 None（usage? 可选）。
    assert normalize_usage("anthropic", {"output_tokens": 21}) == \
        {"inputTokens": None, "outputTokens": 21}


def test_never_leaks_raw_snake_case_field_names():
    out = normalize_usage("anthropic", {"input_tokens": 1, "output_tokens": 2})
    assert set(out.keys()) == {"inputTokens", "outputTokens"}
    assert "input_tokens" not in out and "output_tokens" not in out
