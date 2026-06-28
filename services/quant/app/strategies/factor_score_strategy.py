"""因子组合得分策略 `factor_score`（kind="factor" · 注册）(spec §2.7)。

不是逐根读指标的传统原子策略，而是**消费因子层复合得分**的策略：``prepare`` 解析自包含的
组合规格 ``{members, thresholds}``（创建时由因子组合**内联快照**进 ``params_json``，引擎层零 DB/IO，
评审 C-P1-2），用 ``engine.factors.compose_score`` 算出 ``_score`` 列；``on_candle`` 按阈值产出
多空 / 平仓信号。注册进 ``STRATEGY_REGISTRY`` 后即天然复用既有策略 CRUD / 回测 / 对比 / WF 链路，
作为普通策略**完全继承**既有实时交易护栏（评审 C-P2-8），无需任何表结构变更。

空 / 失效保护：``members`` 为空或某 ``factorKey`` 不在库 / 方向非法 → ``_score=NaN``、安静空仓
（**不**抛错拖垮回测，与 combo 直接抛错不同——factor_score 会进 compare 多策略混排）。配置错误的
显式 400 由创建 / 编辑 API 在边界一次性校验（C1），安静空仓只用于回测运行期。
"""
from __future__ import annotations

import pandas as pd

from ..engine.simulator import BrokerContext
from .base import BaseStrategy, Signal, StrategyMeta, register_strategy

# 阈值默认（评审 C-P1-4）：squash=True 下得分 ∈ [-1,1]，这些阈值才有恒定基准。
DEFAULT_THRESHOLDS: dict = {
    "enterLong": 0.3,
    "enterShort": 0.3,
    "exitBand": 0.1,
    "normWindow": 200,
    "squash": True,
}


def resolve_thresholds(params: dict) -> dict:
    """叠加默认阈值（缺字段回落默认），供 prepare / warmup / on_candle 共用单一口径。"""
    return {**DEFAULT_THRESHOLDS, **(params.get("thresholds") or {})}


@register_strategy
class FactorScoreStrategy(BaseStrategy):
    """读取因子组合复合得分，按阈值产出多空 / 平仓信号（kind="factor"）。"""

    meta = StrategyMeta(
        key="factor_score",
        name="因子组合得分",
        description=(
            "消费独立因子层的复合得分（行情→因子计算→评估/挖掘→权重训练→组合得分→本策略决策）："
            "得分 ≥ 进多阈值做多、≤ -进空阈值做空、落入退出带平仓；仓位强度 = clip(|得分|,0,1)。"
            "组合成员与权重在「因子实验室」训练并内联快照进本策略，严格因果、回测/实时共用同一打分函数。"
        ),
        kind="factor",
        default_params={
            # 自包含快照（创建时由因子组合内联）：members 为空即安静空仓，不抛错。
            "members": [],
            "thresholds": {**DEFAULT_THRESHOLDS},
        },
        # 阈值走通用 ParamEditor；members/weights 由因子实验室 FactorSetBuilder 专属维护。
        params_schema={
            "enterLong": {"type": "number", "default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05,
                          "label": "进多阈值"},
            "enterShort": {"type": "number", "default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05,
                           "label": "进空阈值"},
            "exitBand": {"type": "number", "default": 0.1, "min": 0.0, "max": 1.0, "step": 0.05,
                         "label": "退出带"},
            "normWindow": {"type": "int", "default": 200, "min": 20, "max": 1000, "step": 10,
                           "label": "归一化窗口"},
            "squash": {"type": "bool", "default": True, "label": "tanh 压缩到[-1,1]"},
        },
    )

    def warmup(self) -> int:
        thresholds = resolve_thresholds(self.params)
        return int(thresholds.get("normWindow", 200)) + 60

    def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
        # 延迟导入打破模块装载期循环：engine.factors → signal_edge → strategies →
        # factor_score_strategy（compose_score 仅在每次回测 prepare 调用一次，开销可忽略）。
        from ..engine.factors import compose_score

        df = df.copy()
        members = self.params.get("members") or []
        self._thresholds = resolve_thresholds(self.params)
        if not members:
            df["_score"] = float("nan")
            return df
        try:
            score = compose_score(
                df, members, self.params.get("weights"),
                norm_window=int(self._thresholds.get("normWindow", 200)),
                squash=bool(self._thresholds.get("squash", True)), bar=bar)
            df["_score"] = score.to_numpy()
        except (ValueError, KeyError):
            # 未知 factorKey / 非法 direction：回测期安静空仓（边界校验由创建/编辑 API 负责）。
            df["_score"] = float("nan")
        return df

    def on_candle(self, df: pd.DataFrame, i: int, ctx: BrokerContext) -> list[Signal]:
        score = df["_score"].iat[i]
        if pd.isna(score):
            return []
        thresholds = getattr(self, "_thresholds", DEFAULT_THRESHOLDS)
        exit_band = float(thresholds.get("exitBand", 0.1))
        value = float(score)
        if ctx.position_side == "long":
            return [Signal(side="close", reason="得分落入退出带")] if value <= exit_band else []
        if ctx.position_side == "short":
            return [Signal(side="close", reason="得分落入退出带")] if value >= -exit_band else []
        strength = min(abs(value), 1.0)
        if value >= float(thresholds.get("enterLong", 0.3)):
            return [Signal(side="long", strength=strength, reason="复合得分多头")]
        if value <= -float(thresholds.get("enterShort", 0.3)):
            return [Signal(side="short", strength=strength, reason="复合得分空头")]
        return []
