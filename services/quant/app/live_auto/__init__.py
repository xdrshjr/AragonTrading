"""实时自动交易包（live-auto-trading）：策略驱动的真实 OKX 自动下单 / 平仓。

镜像 `paper/` 实时流，但用 `LiveExecutor` 替代 `SimBroker`，把策略信号翻译为经既有
`okx/trading.py` 服务层的真实下单（继承全部安全护栏与审计）。对外仅暴露注册表与错误类型。
"""
from __future__ import annotations

from .executor import LiveExecutor
from .manager import (
    LiveAutoManager,
    LiveAutoPreflightError,
    LiveAutoSessionLimitError,
    get_live_auto_manager,
)
from .session import LiveAutoSession

__all__ = [
    "LiveAutoManager",
    "LiveAutoSession",
    "LiveExecutor",
    "LiveAutoPreflightError",
    "LiveAutoSessionLimitError",
    "get_live_auto_manager",
]
