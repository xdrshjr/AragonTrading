"""AI 自动交易包（ai-auto-trading）：把当前 AI Agent 作为多策略信号的自动交易决策者。

架构：`signals`（每策略独立 `SimBroker` 影子回放求持久立场，P0-1）+ `decider`（复用 Agent LLM 层，
唯一放行工具 `submit_trade_decision`，失败/超时回落 hold，滚动摘要式上下文压缩）+ `session`
（镜像 `LiveAutoSession` 逐根驱动，`_drive_decision` 替 `_drive_signals`）+ `manager`（单活跃会话
注册表 + 前置门禁 + 与 live-auto 原子互斥）+ `prompts`。真实下单一律经既有 `LiveExecutor`。

共享准入锁访问器 `get_admission_lock` 从 `admission` 直接导出（零重依赖）；manager 符号采用 PEP 562
`__getattr__` **惰性导出**，避免 `live_auto/manager.py` 顶层 `from ..ai_auto.admission import ...`
触发 `ai_auto → live_auto → ai_auto` 循环 import（P1-4 实现注）。
"""
from __future__ import annotations

from .admission import get_admission_lock

__all__ = [
    "AiTradeManager",
    "AiTradePreflightError",
    "AiTradeSessionLimitError",
    "get_admission_lock",
    "get_ai_trade_manager",
]

_LAZY = {"AiTradeManager", "AiTradePreflightError", "AiTradeSessionLimitError",
         "get_ai_trade_manager"}


def __getattr__(name: str):  # PEP 562：惰性导入 manager 符号，打破与 live_auto 的循环依赖。
    if name in _LAZY:
        from . import manager
        return getattr(manager, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
