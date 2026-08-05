"""搜索器子包（strategy-factor-discovery §2.4）。

**范围（D-6）**：v1 只有 ``factor`` 轴。``factor_combo`` / ``strategy_param`` /
``strategy_combo`` 三轴是 v1.1+ 的加法增量，落地前一律不进 ``IMPLEMENTED_AXES``、
UI 不渲染、API 400——绝不留「能选但不产候选」的空转轴。
"""
from __future__ import annotations

from .factor_searcher import FactorSearcher  # noqa: F401

__all__ = ["FactorSearcher"]
