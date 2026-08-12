"""OKX 链路重试的**纯核心**（okx-link-resilience §2.3.1/§2.3.2）。

本模块只有函数与不可变数据类：**无 IO、无全局状态、随机源与时钟经形参注入**，故故障注入测试
既不需要网络也不需要真实 sleep。它回答三个问题：

1. 这次失败**该不该重试**（``classify_*`` 三件套）——今天 ``rest_client._request`` 对所有 4xx
   一视同仁地空转三次，把「你传错了」说成「连不上交易所」；
2. 下一次**等多久**（``next_delay`` 去相关抖动 + ``parse_retry_after`` 尊重交易所指令）——纯指数
   退避会让多标的分组在同一毫秒一起重试，制造自我同步的请求尖峰；
3. WS 收发循环**下一跳多久醒一次**（``tick_timeout``），使活性判据的检查延迟 ≤1s 又不忙轮询。
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx

Outcome = Literal["ok", "retryable", "rate_limited", "terminal", "clock_skew"]

# OKX 侧**明确的暂时态**业务码：其余业务码（参数 / 余额 / 权限）重试只会浪费配额并延长用户等待。
_RETRYABLE_OKX_CODES = {"50001", "50004", "50011", "50013", "50026"}
_RATE_LIMITED_OKX_CODES = {"50011"}
# 签名时间戳过期 / 失效：可经服务器时钟对齐自愈（§2.4.2），与普通重试分开归类。
_CLOCK_SKEW_CODES = {"50102", "50104"}
# 传输层异常白名单：这些都是「请求没走完」，重试有意义。
_RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
    httpx.PoolTimeout, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError,
)


def classify_status(status: int) -> Outcome:
    """HTTP 状态码 → 处置。429 单列（限频），5xx / 408 / 425 可重试，其余 4xx 一律终态。"""
    if status == 429:
        return "rate_limited"
    if status in (408, 425, 500, 502, 503, 504):
        return "retryable"
    if 200 <= status < 300:
        return "ok"
    return "terminal"


def classify_exception(exc: BaseException) -> Outcome:
    """异常 → 处置。只有传输层异常可重试；其余（含解析错误）一律终态。"""
    return "retryable" if isinstance(exc, _RETRYABLE_EXCEPTIONS) else "terminal"


def classify_okx_code(code: str) -> Outcome:
    """OKX 业务码 → 处置。``"0"`` 即成功；时钟偏差单列（可自愈）；其余按暂时态白名单。"""
    text = str(code)
    if text == "0":
        return "ok"
    if text in _CLOCK_SKEW_CODES:
        return "clock_skew"
    if text in _RATE_LIMITED_OKX_CODES:
        return "rate_limited"
    if text in _RETRYABLE_OKX_CODES:
        return "retryable"
    return "terminal"


@dataclass(frozen=True)
class RetryPolicy:
    """单次逻辑调用的重试预算。``budget_s`` **含等待时间**，从进入调用起算。"""

    max_attempts: int
    base_delay_s: float
    max_delay_s: float
    budget_s: float


def next_delay(prev_delay: float, policy: RetryPolicy,
               rand: Callable[[float, float], float] = random.uniform) -> float:
    """去相关抖动退避：``min(max_delay, uniform(base, prev × 3))``。

    为什么不是纯指数：N 个会话在同一次网络抖动里同时失败时，无抖动的 ``0.4 × 2^n`` 会让它们在
    同一毫秒一起重试，把一次抖动放大成一次限频事故（``docs/diagnoses/live-auto-multi-symbol-
    rate-limit-429`` 记录的正是这个形状）。

    Args:
        prev_delay: 上一次实际等待秒数；``<=0`` 时以 ``base_delay_s`` 起步。
        policy: 退避参数（只读 ``base_delay_s`` / ``max_delay_s``）。
        rand: 均匀分布随机源，测试注入确定性替身。

    Returns:
        本次应等待的秒数，夹在 ``[0, max_delay_s]``。
    """
    base = max(0.0, policy.base_delay_s)
    upper = max(base, prev_delay * 3.0 if prev_delay > 0 else base)
    return max(0.0, min(policy.max_delay_s, rand(base, upper)))


def parse_retry_after(value: str | None) -> float | None:
    """解析 ``Retry-After`` 头（只认整秒 / 浮点秒形式）；不可解析或非正数一律 ``None``。

    刻意不支持 HTTP-date 形式：OKX 不发它，而支持一个用不到的格式会引入一处需要本机时钟正确
    才能算对的分支——本轮的前提恰恰是本机时钟可能是错的。
    """
    if value is None:
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def tick_timeout(idle_s: float, ping_interval_s: float,
                 floor_s: float = 0.05, ceil_s: float = 1.0) -> float:
    """WS 收发循环下一跳的等待上限：距「该发 ping 了」还剩多久，夹在 ``[floor_s, ceil_s]``。

    夹上界保证任何活性判据的检查延迟 ≤1s；夹下界避免退化成忙轮询。
    ``ping_interval_s <= 0``（判据关闭）时直接返回上界。
    """
    if ping_interval_s <= 0:
        return ceil_s
    remaining = ping_interval_s - max(0.0, idle_s)
    return max(floor_s, min(ceil_s, remaining))
