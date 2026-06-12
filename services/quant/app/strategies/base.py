"""可插拔策略框架 (spec §5.1)。

- `STRATEGY_REGISTRY` 是 meta（name/description/default_params/params_schema/策略类）的**唯一真源**(C6)。
- 新增策略 = 新文件 + `@register_strategy`，无需改引擎/接口。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from ..engine.simulator import BrokerContext, RiskParams


class Signal(BaseModel):
    """策略在某根 K 线上产生的交易信号。"""

    side: Literal["long", "short", "close"]
    strength: float = Field(default=1.0, ge=0.0, le=1.0)  # 0~1 仓位比例
    reason: str = ""                                       # marker tooltip 用


class StrategyMeta(BaseModel):
    """策略模板元信息（注册表唯一真源）。"""

    key: str               # 唯一标识，如 "long_shadow_reversal"
    name: str
    description: str
    default_params: dict
    params_schema: dict     # 与前端 ParamSchema 对齐


STRATEGY_REGISTRY: dict[str, type["BaseStrategy"]] = {}


def register_strategy(cls: type["BaseStrategy"]) -> type["BaseStrategy"]:
    """装饰器：把策略类登记进注册表（key 重复即报错，避免静默覆盖）。"""
    key = cls.meta.key
    if key in STRATEGY_REGISTRY and STRATEGY_REGISTRY[key] is not cls:
        raise ValueError(f"策略 key 重复注册: {key!r}")
    STRATEGY_REGISTRY[key] = cls
    return cls


def get_strategy_class(key: str) -> type["BaseStrategy"] | None:
    return STRATEGY_REGISTRY.get(key)


class BaseStrategy(ABC):
    """所有策略的基类。子类须提供类属性 `meta` 并实现 `on_candle`。"""

    meta: StrategyMeta

    def __init__(self, params: dict | None = None):
        # 合并：默认参数 <- 实例覆盖
        self.params: dict = {**self.meta.default_params, **(params or {})}

    # ---- 可选 hook：在回测前批量计算指标列（向量化，算一次）----
    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        return df

    def warmup(self) -> int:
        """开始产生信号前需要的最小历史根数。"""
        return 20

    def risk_params(self) -> RiskParams:
        """从 params 提取统一风控参数（供 SimBroker 使用）。"""
        p = self.params
        return RiskParams(
            stop_loss_pct=p.get("stop_loss_pct"),
            take_profit_pct=p.get("take_profit_pct"),
            max_hold_bars=p.get("max_hold_bars"),
        )

    @abstractmethod
    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        """返回第 i 根产生的信号列表（df 含截至 i 的全部 K 线 + 指标列）。"""
        raise NotImplementedError
