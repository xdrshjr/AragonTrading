"""下单前二次反思裁决的落地纯逻辑（ai-trade-pre-trade-reflection §2.3，session/backtest 共用）。

把「反思裁决 → 最终决策」的收敛映射抽为无副作用纯函数，避免 decider 膨胀、便于单测。**首要不变式**：
反思只会让执行**更保守**——可以否决（→ hold）或**收缩**仓位，但**永远不能**放大仓位、反转方向、把
hold 变交易。结构上只实现「否决 / 收缩」两种收敛操作，方向永不改写（§七 R-7）。
"""
from __future__ import annotations

from dataclasses import replace

from .decider import ReflectionVerdict, TradeDecision, _hold

_OPEN_ACTIONS = ("open_long", "open_short")


def apply_reflection(decision: TradeDecision, degrade: str | None,
                     verdict: ReflectionVerdict | None,
                     position_step: float = 0.0) -> tuple[TradeDecision, str | None]:
    """把反思裁决落到最终决策（只会更保守）；返回 `(decision, degrade_reason)`。

    - `verdict` 为 None 或 `ran=False`（反思关闭/未触发）→ 原样透传（逐字零回归）。
    - fail-closed（`degrade_reason` 非空）或 `abort` → 降级为 `hold`：决策理由改写为反思理由（§5.3），
      `degrade` 归并为 `degrade or code`（code ∈ {reflection_abort, reflection_timeout,
      reflection_llm_error, reflection_no_tool, reflection_invalid}）。
    - `confirm` 且给了收缩值（仅 `open_*`）→ **先**量化吸附到合法档位，**再**对原档位取 `min` 作最后一步
      （RV-4：`quantize` 用 `round()` 就近取整可能把调整值抬到 ≥ 原档位，故 `min` 收口是「只减不增」硬保证；
      无论舍入方向最终值都 ≤ 原档位）；否则原样返回。

    Args:
        decision: 共识门禁后的本轮决策（交易意愿）。
        degrade: 决策器/共识门禁先前的降级原因（None 表示无）。
        verdict: 反思裁决（`AiTradeDecider.reflect` 产物）。
        position_step: 开仓档位步长（fraction；用于收缩值量化，0=连续）。

    Returns:
        `(final_decision, degrade_reason)`——`final_decision` 同方向或更保守。
    """
    if verdict is None or not verdict.ran:
        return decision, degrade
    code = verdict.degrade_reason or ("reflection_abort" if verdict.verdict == "abort" else None)
    if code is not None:                                 # fail-closed 或 abort → 降级为 hold
        return _hold(verdict.reason or code), (degrade or code)
    # confirm：仅当模型给了严格更小的收缩值时收窄仓位（绝不放大/改向）。
    adjusted = verdict.adjusted_size_fraction
    if (adjusted is not None and decision.action in _OPEN_ACTIONS
            and decision.size_fraction is not None):
        from .signals import quantize_size_fraction      # 延迟导入打破 signals→decider 模块级环（§八）
        quantized = quantize_size_fraction(adjusted, position_step)
        new_size = min(decision.size_fraction, quantized)   # RV-4：量化在前、对原值 clamp 收口（只减不增）
        if new_size < decision.size_fraction:
            # ai-trade-limit-orders-fix F-1（P0）：位置重建是「加字段就丢一遍」的复发根——曾把
            # ord_type/limit_px 静默落回 market/None（限价单被反思收缩后变市价成交）。`replace`
            # 只改仓位、其余字段（含未来新增）原样保留，根治该复发模式，勿回改为位置构造。
            return replace(decision, size_fraction=new_size), degrade
    return decision, degrade


def applied_shrink(original: TradeDecision, final: TradeDecision) -> float | None:
    """反思实际生效的收缩后开仓仓位（仅 `open_*` 且严格变小时返回，否则 None；供轨迹展示实际档位）。"""
    if (final.action in _OPEN_ACTIONS and final.size_fraction is not None
            and original.size_fraction is not None
            and final.size_fraction < original.size_fraction):
        return final.size_fraction
    return None
