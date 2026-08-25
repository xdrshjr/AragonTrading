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
                     position_step: float = 0.0, *,
                     fail_open_actions: tuple[str, ...] = ()
                     ) -> tuple[TradeDecision, str | None, str | None]:
    """把反思裁决落到最终决策（只会更保守）；返回 `(decision, degrade_reason, fail_open_code)`。

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
        fail_open_actions: 对哪些动作把 **fail-closed 反转为 fail-open**（exit-priority X-6）；
            默认 `()` ⇒ **逐字回退**到本特性合入前（C-8 的一键回退据此成立）。会话侧传
            `("close",)`：`close` 是风险收敛动作，一次 LLM 超时不该锁死一笔真实持仓——而
            `hold_horizon.py` 模块头早已把同一条纪律写成不变式，只是在本模块被写反了。

    Returns:
        `(final_decision, degrade_reason, fail_open_code)`——`final_decision` 同方向或更保守；
        第三元非 `None` ⇒ 本轮是**因基础设施失败而放行**的那一笔（它**绝不进 `degrade`**：
        `degrade` 的语义是「本轮被降级」，把放行塞进去会让闸计数把一次成功离场记成一次被拦）。
        `fail_open_actions=()` 时第三元恒为 `None`。
    """
    if verdict is None or not verdict.ran:
        return decision, degrade, None
    code = verdict.degrade_reason or ("reflection_abort" if verdict.verdict == "abort" else None)
    if code is not None:                                 # fail-closed 或 abort → 降级为 hold
        # X-6：**只有基础设施失败**（`degrade_reason` 非空）才反转；模型的真实终态裁决
        # （`abort` ⇒ `degrade_reason is None` ⇒ code 恒为 `reflection_abort`）逐字不变。
        if verdict.degrade_reason is not None and decision.action in fail_open_actions:
            return decision, degrade, code
        return _hold(verdict.reason or code), (degrade or code), None
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
            return replace(decision, size_fraction=new_size), degrade, None
    return decision, degrade, None


def build_reflection_note(original: TradeDecision, verdict: ReflectionVerdict | None,
                          final: TradeDecision, *,
                          pending_exit: dict | None = None) -> dict | None:
    """把本轮反思结果压成记忆注记（hold-inertia-fix F2，session/backtest 共用纯函数）。

    否决把开仓候选改写成普通 hold 后，journal/摘要素材里「曾经想开仓但被否决」完全蒸发，摘要器
    反而把长期空仓总结为「观望被验证正确」——本注记随 `commit_round(reflection=…)` 落 journal
    （并在否决轮富化 tool_result），让摘要器与短窗口上下文都能看到被否决的原意图。

    Args:
        original: 反思前的提议决策（须在 `apply_reflection` 之前留存引用）。
        verdict: 反思裁决；`None` 或 `ran=False`（关闭/未触发）→ 返回 `None`（journal 该键为 None）。
        final: `apply_reflection` 后的最终决策。
        pending_exit: keyword-only，默认 `None` → 既有调用零回归（exit-priority §4.4.2）。本轮是
            被持仓周期层**挂起待裁决**的离场提议时的断面
            `{"gateReason", "intentStreak", "overrideRounds"}`。落进注记而不只落进 prompt 是有理由
            的：`_context` 会被滚动摘要重置，而「那一次是复核官替我放行 / 否决的」是事后问责回路
            里最该留下来的一条——缺了它，摘要器只能看到一次普通的 close。

    Returns:
        注记 dict 或 `None`。`failClosed=True` 表示基础设施故障（timeout/llm_error/no_tool/invalid）
        的保守跳过而非模型真实否决——摘要器须区分（F3 head 指令）。
    """
    if verdict is None or not verdict.ran:
        return None
    return {"verdict": verdict.verdict,
            "pendingExit": pending_exit,
            "vetoed": final.action != original.action,   # 被否决（open_*/close → hold）
            "failClosed": verdict.degrade_reason is not None,
            "reason": verdict.reason,
            "proposedAction": original.action, "proposedSizeFraction": original.size_fraction,
            "proposedConfidence": original.confidence,
            "finalAction": final.action, "finalSizeFraction": final.size_fraction}


def applied_shrink(original: TradeDecision, final: TradeDecision) -> float | None:
    """反思实际生效的收缩后开仓仓位（仅 `open_*` 且严格变小时返回，否则 None；供轨迹展示实际档位）。"""
    if (final.action in _OPEN_ACTIONS and final.size_fraction is not None
            and original.size_fraction is not None
            and final.size_fraction < original.size_fraction):
        return final.size_fraction
    return None
