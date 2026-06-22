"""_redact 纯函数单测（H2）：含 key→脱敏、短串 / 空串→原样、不误伤无关文本。"""
from __future__ import annotations

from app.agent.providers import _redact


def test_redact_replaces_key_substring():
    out = _redact("auth failed for key=sk-XXXXabcd here", "sk-XXXXabcd")
    assert "sk-XXXXabcd" not in out
    assert "····" in out


def test_redact_short_secret_untouched():
    # 长度 <6 的串不替换，避免短串误伤无关文本
    assert _redact("error code 12345 occurred", "12345") == "error code 12345 occurred"


def test_redact_empty_secret_noop():
    assert _redact("plain message", "") == "plain message"
    assert _redact("plain message", "   ") == "plain message"


def test_redact_unrelated_text_unchanged():
    assert _redact("totally unrelated text", "sk-abcdef123456") == "totally unrelated text"


def test_redact_handles_repeated_occurrences():
    out = _redact("key sk-secret9 then sk-secret9 again", "sk-secret9")
    assert "sk-secret9" not in out
