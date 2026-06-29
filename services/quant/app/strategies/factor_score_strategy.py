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

# 主要驱动因子判定（auto-trade-explainability §四）：贡献绝对值前 N 且占比达阈即标记为 top driver。
_TOP_DRIVER_COUNT = 3
_TOP_DRIVER_SHARE = 0.15


def resolve_thresholds(params: dict) -> dict:
    """叠加默认阈值（缺字段回落默认），供 prepare / warmup / on_candle 共用单一口径。"""
    return {**DEFAULT_THRESHOLDS, **(params.get("thresholds") or {})}


def _finite_at(series: pd.Series, i: int) -> float | None:
    """取第 i 根有限值（暖机 NaN / 越界回 None），供因子断面提取防御性读取。"""
    try:
        value = series.iat[i]
    except (IndexError, KeyError):
        return None
    return float(value) if pd.notna(value) else None


def _factor_metrics(value: float, thresholds: dict) -> list[dict]:
    """复合得分 vs 阈值的通用指标对照（进多 / 进空 / 退出带 / 仓位强度）。"""
    tone = "long" if value > 0 else ("short" if value < 0 else "neutral")
    return [
        {"label": "复合得分", "value": f"{value:.4f}", "tone": tone},
        {"label": "进多阈值", "value": f"{float(thresholds.get('enterLong', 0.3)):.2f}"},
        {"label": "进空阈值", "value": f"-{float(thresholds.get('enterShort', 0.3)):.2f}"},
        {"label": "退出带", "value": f"±{float(thresholds.get('exitBand', 0.1)):.2f}"},
        {"label": "仓位强度", "value": f"{min(abs(value), 1.0):.2f}"},
    ]


def _factor_rows(breakdown, i: int) -> list[dict]:
    """从 ScoreBreakdown 第 i 根断面抽出逐成员归因行（含 share / isTopDriver）。"""
    rows: list[dict] = []
    for member in breakdown.members:
        rows.append({
            "factorKey": member.factor_key, "name": member.name,
            "direction": member.direction, "weight": round(member.weight, 6),
            "value": round(_finite_at(member.value, i) or 0.0, 6),
            "zscore": round(_finite_at(member.zscore, i) or 0.0, 6),
            "contribution": round(_finite_at(member.contribution, i) or 0.0, 6),
        })
    total_abs = sum(abs(r["contribution"]) for r in rows)
    ranked = sorted(range(len(rows)), key=lambda k: abs(rows[k]["contribution"]), reverse=True)
    top = set(ranked[:_TOP_DRIVER_COUNT])
    for idx, row in enumerate(rows):
        share = (abs(row["contribution"]) / total_abs) if total_abs > 0 else 0.0
        row["share"] = round(share, 6)
        row["isTopDriver"] = idx in top and share >= _TOP_DRIVER_SHARE
    return rows


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
        # factor_score_strategy（score_breakdown 仅在每次回测 prepare 调用一次，开销可忽略）。
        from ..engine.factors import resolve_norm_contract, score_breakdown

        df = df.copy()
        members = self.params.get("members") or []
        self._thresholds = resolve_thresholds(self.params)
        # 因子归因断面缓存（与 df 等长，暖机段 NaN）：on_candle 据此组装 Signal.explain；
        # 空成员 / 解析失败回 None，解释回落执行器通用构造器（不影响信号本身）。
        self._breakdown = None
        if not members:
            df["_score"] = float("nan")
            return df
        # 归一化契约单一真源（H1/H2）：优先用训练写入快照的 normStats（窗口/clip），无则回落 thresholds。
        norm_window, clip = resolve_norm_contract(
            norm_stats=self.params.get("normStats"), thresholds=self._thresholds)
        try:
            # 保留传 self.params.get("weights")（R-P0-1 向后兼容硬约束）：旧快照走顶层 weights、
            # 新快照（weights=None）走成员烘焙 weight，绝不因「权重单源」让旧策略全员 0 权重静默不交易。
            # 改调 score_breakdown 取 .score（与旧 compose_score 数值逐位等价）并缓存逐成员明细。
            breakdown = score_breakdown(
                df, members, self.params.get("weights"),
                norm_window=norm_window, clip=clip,
                squash=bool(self._thresholds.get("squash", True)), bar=bar)
            df["_score"] = breakdown.score.to_numpy()
            self._breakdown = breakdown
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
            if value <= exit_band:
                return [Signal(side="close", reason="得分落入退出带",
                               explain=self._build_explain(i, "close", value, thresholds))]
            return []
        if ctx.position_side == "short":
            if value >= -exit_band:
                return [Signal(side="close", reason="得分落入退出带",
                               explain=self._build_explain(i, "close", value, thresholds))]
            return []
        strength = min(abs(value), 1.0)
        if value >= float(thresholds.get("enterLong", 0.3)):
            return [Signal(side="long", strength=strength, reason="复合得分多头",
                           explain=self._build_explain(i, "long", value, thresholds))]
        if value <= -float(thresholds.get("enterShort", 0.3)):
            return [Signal(side="short", strength=strength, reason="复合得分空头",
                           explain=self._build_explain(i, "short", value, thresholds))]
        return []

    def _build_explain(self, i: int, side: str, value: float, thresholds: dict) -> dict | None:
        """组装 kind="factor_score" 的 TriggerExplain（第 i 根因子归因断面 + 复合得分对照）。

        全程异常隔离：构建失败回 None（执行器随后回落通用构造器据 reason 合成），绝不影响信号本身。
        """
        breakdown = getattr(self, "_breakdown", None)
        if breakdown is None:
            return None
        try:
            headline = {"long": "复合得分多头", "short": "复合得分空头",
                        "close": "得分落入退出带"}.get(side, "因子组合信号")
            return {
                "schema": 1, "kind": "factor_score", "side": side, "headline": headline,
                "summary": "因子组合复合得分触发；下方为本次决策各成员因子的方向化归因。",
                "metrics": _factor_metrics(value, thresholds),
                "composite": {
                    "score": round(value, 6),
                    "preSquash": round(_finite_at(breakdown.composite, i) or value, 6),
                    "squash": bool(breakdown.squash),
                    "enterLong": float(thresholds.get("enterLong", 0.3)),
                    "enterShort": float(thresholds.get("enterShort", 0.3)),
                    "exitBand": float(thresholds.get("exitBand", 0.1)),
                },
                "factors": _factor_rows(breakdown, i),
            }
        except Exception:  # noqa: BLE001 —— 解释构建失败不拖累信号（异常隔离，R1）
            return None
