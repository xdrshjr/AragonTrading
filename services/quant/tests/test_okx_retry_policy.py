"""OKX 重试纯核心单测（okx-link-resilience §2.3.1 / §2.3.2）。

守的是三条判据：**该不该重试**、**等多久**、**WS 收发循环多久醒一次**。全部无 IO、无真实 sleep。
"""
from __future__ import annotations

import httpx
import pytest

from app.okx.retry import (
    RetryPolicy,
    classify_exception,
    classify_okx_code,
    classify_status,
    next_delay,
    parse_retry_after,
    tick_timeout,
)


@pytest.mark.parametrize(("status", "expected"), [
    (200, "ok"), (201, "ok"), (299, "ok"),
    (429, "rate_limited"),
    (408, "retryable"), (425, "retryable"), (500, "retryable"),
    (502, "retryable"), (503, "retryable"), (504, "retryable"),
    (400, "terminal"), (401, "terminal"), (403, "terminal"), (404, "terminal"),
    (301, "terminal"),
])
def test_classify_status_covers_every_branch(status, expected):
    assert classify_status(status) == expected


def test_a_400_is_terminal_so_it_is_never_retried():
    """根因 C-1 的正面判据：一个 400（参数不合法）重试三次只会把「你传错了」说成「连不上」。"""
    assert classify_status(400) == "terminal"
    assert classify_status(500) == "retryable"


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("x"), httpx.ConnectTimeout("x"), httpx.ReadTimeout("x"),
    httpx.WriteTimeout("x"), httpx.PoolTimeout("x"), httpx.RemoteProtocolError("x"),
    httpx.ReadError("x"), httpx.WriteError("x"),
])
def test_transport_exceptions_are_retryable(exc):
    assert classify_exception(exc) == "retryable"


@pytest.mark.parametrize("exc", [ValueError("bad json"), KeyError("k"), RuntimeError("boom")])
def test_non_transport_exceptions_are_terminal(exc):
    assert classify_exception(exc) == "terminal"


@pytest.mark.parametrize(("code", "expected"), [
    ("0", "ok"),
    ("50102", "clock_skew"), ("50104", "clock_skew"),
    ("50011", "rate_limited"),
    ("50001", "retryable"), ("50004", "retryable"),
    ("50013", "retryable"), ("50026", "retryable"),
    ("51008", "terminal"), ("50111", "terminal"), ("", "terminal"),
])
def test_classify_okx_code_table(code, expected):
    assert classify_okx_code(code) == expected


_POLICY = RetryPolicy(max_attempts=4, base_delay_s=0.4, max_delay_s=8.0, budget_s=25.0)


def test_next_delay_starts_at_base_and_is_bounded():
    """首跳从 base 起步；上界永远夹在 `max_delay_s` 内（注入确定性 rand 后即公式本身）。"""
    assert next_delay(0.0, _POLICY, rand=lambda lo, hi: lo) == pytest.approx(0.4)
    assert next_delay(0.0, _POLICY, rand=lambda lo, hi: hi) == pytest.approx(0.4)
    # prev × 3 超过上限时被夹住，不会无限增长
    assert next_delay(100.0, _POLICY, rand=lambda lo, hi: hi) == pytest.approx(8.0)


def test_next_delay_is_decorrelated_jitter_not_pure_exponential():
    """去相关抖动：上界 = prev × 3，且取值区间真的是 [base, prev×3]（非一个确定的数）。

    为什么必须有抖动：N 个会话在同一次网络抖动里同时失败时，无抖动的 `0.4 × 2^n` 会让它们在
    同一毫秒一起重试，把一次抖动放大成一次限频事故。
    """
    low = next_delay(1.0, _POLICY, rand=lambda lo, hi: lo)
    high = next_delay(1.0, _POLICY, rand=lambda lo, hi: hi)
    assert low == pytest.approx(0.4)
    assert high == pytest.approx(3.0)
    assert low < high


def test_next_delay_never_negative():
    assert next_delay(-5.0, _POLICY, rand=lambda lo, hi: lo) >= 0.0


@pytest.mark.parametrize(("raw", "expected"), [
    ("3", 3.0), ("0.5", 0.5), (" 2 ", 2.0),
    (None, None), ("", None), ("abc", None), ("0", None), ("-1", None),
    ("Wed, 21 Oct 2026 07:28:00 GMT", None),   # HTTP-date 刻意不支持（本轮前提是本机时钟可能是错的）
])
def test_parse_retry_after(raw, expected):
    assert parse_retry_after(raw) == expected


def test_tick_timeout_is_clamped_so_liveness_checks_stay_under_one_second():
    assert tick_timeout(0.0, 15.0) == 1.0          # 上界：判据检查延迟 ≤1s
    assert tick_timeout(14.99, 15.0) == 0.05       # 下界：不退化成忙轮询
    assert tick_timeout(20.0, 15.0) == 0.05        # 已过期也不返回负数
    assert tick_timeout(5.0, 15.0) == 1.0
    assert tick_timeout(14.5, 15.0) == pytest.approx(0.5)
    assert tick_timeout(0.0, 0.0) == 1.0           # 判据关闭 ⇒ 直接返回上界
