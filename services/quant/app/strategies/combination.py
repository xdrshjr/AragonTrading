"""组合策略模板 `regime_rotation`（体制自适应策略组合 · 注册）。

组合不是逐根撮合的原子策略，而是一种**编排型**模板：它声明成员清单与轮动参数，
由 ``engine.combo.run_combo_backtest`` 以「成员净值轮动」执行（每个成员独立回测后，
在再平衡边界因果选出近端表现最优的成员拼接组合净值）。注册进 ``STRATEGY_REGISTRY``
后即天然复用既有策略 CRUD / 回测 / 对比 / 运行历史链路，无需任何表结构变更。

``on_candle`` 显式抛错（C1：失败要响，不静默）——任何误把组合喂进 ``run_backtest``
的路径都会立即暴露，而非产出一条静默错误的净值。
"""
from __future__ import annotations

import pandas as pd

from ..engine.simulator import BrokerContext
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy


@register_strategy
class CombinationStrategy(BaseStrategy):
    """趋势自适应策略组合（regime rotation）。

    成员清单与轮动参数存于 ``params``（``members`` + 轮动标量），由 combo 编排层读取。
    """

    meta = StrategyMeta(
        key="regime_rotation",
        name="趋势自适应策略组合",
        description=(
            "按时间段自动识别市场体制（趋势上行/下行/区间）并轮动到近端表现最优的成员策略；"
            "决策滞后 1 根、成员回测沿用次根开盘成交，严格因果无未来函数；"
            "含体制门控 + 切换迟滞 + 最短持有 + 再平衡间隔，抑制频繁切换与过拟合。"
            "仅支持回测/对比（不接入实时模拟）。"
        ),
        kind="combo",
        default_params={
            # 默认成员均为已存在的内置 key（保证 seed 后即可跑通一次 preview）。
            # 含 ema_cross_scalper（trend_up/trend_down 双命中）→ 趋势体制下候选 ≥2，
            # 使 selectionMetric 选优在竞争者间真正生效，而非仅按体制切换（R6）。
            "members": [
                {"templateKey": "trend_pullback_long", "params": {}},
                {"templateKey": "ema_cross_scalper", "params": {}},
                {"templateKey": "range_scalping_strategy", "params": {}},
                {"templateKey": "trend_pullback_short", "params": {}},
            ],
            "rebalanceBars": 24,
            "lookbackBars": 168,
            "selectionMetric": "sharpe",
            "minDwellBars": 24,
            "switchMarginPct": 0.10,
            "regimeGating": True,
            "turnoverBps": 5.0,
        },
        # 仅轮动标量参数走通用 ParamEditor；members 由前端 ComboView 专属编辑器维护。
        params_schema={
            "rebalanceBars": {"type": "int", "default": 24, "min": 4, "max": 240, "step": 1,
                              "label": "再平衡间隔(根)"},
            "lookbackBars": {"type": "int", "default": 168, "min": 24, "max": 1000, "step": 1,
                             "label": "表现回看(根)"},
            "selectionMetric": {"type": "enum", "default": "sharpe",
                                "options": ["return", "sharpe", "calmar"], "label": "选优指标"},
            "minDwellBars": {"type": "int", "default": 24, "min": 1, "max": 240, "step": 1,
                             "label": "最短持有(根)"},
            "switchMarginPct": {"type": "number", "default": 0.10, "min": 0.0, "max": 1.0,
                                "step": 0.01, "label": "切换迟滞%"},
            "regimeGating": {"type": "bool", "default": True, "label": "按体制限定候选"},
            "turnoverBps": {"type": "number", "default": 5.0, "min": 0.0, "max": 50.0, "step": 0.5,
                            "label": "换手成本(bps)"},
        },
    )

    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        raise RuntimeError(
            "CombinationStrategy 必须经 engine.combo.run_combo_backtest 执行，不可直接进 run_backtest"
        )
