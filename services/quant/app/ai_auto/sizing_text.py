"""仓位 / 刹车层的中文文案真源（宪章段 + 每轮 prompt 段 + 回测免责，§3.8/§3.10）。

**渲染与判定同源**：prompt 里写出来的上限、止损距离、刹车档位全部由本模块从同一份 wire 渲染，杜绝
「UI 说 0.5、系统执行 0.25」的信任崩塌。任一键缺席即整句省略（不假数据、不写「当前无 xx」的噪音）。
"""
from __future__ import annotations

from .drawdown_brake import BrakeRule
from .sizing import SizingRule, StopRule

# 刹车档位的中文（后端侧文案真源；前端 `describeSizing` 与本表逐条对应）。
_BRAKE_LABEL = {
    "none": "未触发",
    "slow": "第一档减速（开新仓的仓位系数被下调）",
    "halt": "第二档只平不开（回撤越线，开仓暂停，平仓不受影响）",
    "cooldown": "第三档连亏冷却（连续亏损达阈，开仓暂停至冷却到期）",
}

# 回测侧的偏差免责（与既有 `_PROFIT_GUARD_BACKTEST_NOTE` / `_EDGE_GATE_BACKTEST_NOTE` 同源同展示）。
# 绝不宣称等同实盘——回测接入确定性层最大的风险恰恰是「曲线变好看 ⇒ 用户高估实盘」。
SIZING_BACKTEST_NOTE = (
    "仓位与刹车层在回测中的**六**条已知偏差：① 组合相关性预算不生效（AI 回测是单标的运行器，组合"
    "相关性在结构上不存在）；② 已实现波动率取自本根之前的收盘序列，与实盘的滚动窗口一致，但实盘可能"
    "在两根之间被墙钟唤醒并用同一根的旧波动率求解；③ ATR 止损经 `SimBroker.apply_risk` 的**根内保守"
    "成交序**判定（止损优先于止盈），与交易所侧 OCO 的实时触发不同构；④ 连亏冷却按根 ts 计时，实盘按"
    "墙钟计时，定时唤醒会使实盘的冷却覆盖更多决策轮；⑤ 回测无限价挂单与反手在飞态，故实盘的"
    "`skipped_resting` / `skipped_position_live` 两条**跳过改写**分支在回测中永不出现——回测里 ATR "
    "止损的覆盖率天然高于实盘；⑥ 凯利项在回测中**恒不参与** `min`（回测运行器不加载信号先验快照，"
    "`kellyStatus` 恒为 `no_prior`），故回测只验证波动率目标项、刹车与 ATR 止损三件事。")


def format_sizing_charter(rule: SizingRule | None, stop: StopRule | None,
                          brake: BrakeRule | None = None) -> str:
    """系统 prompt 的仓位宪章章；三条规则全未装配 ⇒ `""` ⇒ 渲染逐字节不变（零回归）。

    极性必须写清楚：本层**只会调小**模型给出的 `sizeFraction`。给出高于上限的值不会报错，但会被系统
    按上限执行——把这件事藏起来会让模型反复给出被静默改写的数字，并把「我下了 0.75」写进自己的摘要。
    """
    if rule is None and stop is None and brake is None:
        return ""
    lines = ["【仓位与风险层 · 系统强制执行】"]
    if rule is not None:
        lines.append(
            f"· 你给出的 sizeFraction 是**上限提议**：系统会按当前已实现波动率（年化目标 "
            f"{rule.vol_target_annual:.0%}）、胜率先验、往返成本与组合敞口独立解出一个上限，"
            "并取两者较小值执行——**只会被调小、不会被调大**。")
    if stop is not None:
        lines.append(
            f"· 止损距离按确认根 ATR 定标为 {stop.stop_atr_mult:g}×ATR、止盈为其 {stop.stop_rr:g} 倍，"
            f"在开仓那一刻随单挂到交易所侧；不再是固定百分比。{_stop_floor_clause(stop)}")
    if brake is not None:
        lines.append(
            "· 会话权益自峰值回撤触及第一档即下调开新仓的仓位系数，触及第二档转为**只平不开**，"
            "连续亏损达阈进入冷却。三档**全部只拦开新仓**：平仓与撤单任何时候都不会被拦。"
            "回撤按 mark-to-market 口径（含未实现盈亏）计算。")
    return "\n".join(lines)


def _stop_floor_clause(stop: StopRule) -> str:
    """止损的**成本下界**子句（tradability-and-open-funnel 轴 B）；未装配（`0`）⇒ `""` ⇒ 逐字回退。

    这是轴 B 面向模型的唯一披露落点，且刻意与上一句同段：止损位是模型自己在提案里给的，而系统会把
    落进往返成本带以内的止损**抬出去**——不说，模型就会以为它那个比手续费还窄的止损真的生效了。
    实测依据：BTC 1m 上 `2×ATR` 的止损距离中位 11.2 bps，而一笔市价往返成本 13.0 bps，
    **62.3% 的止损被打掉时亏的价格幅度还不如手续费多**。

    只写倍数、不写绝对 bps：宪章章在会话启动时装配一次，而往返成本逐轮现算（费率档位 / 价差 /
    资金费都会变）。在这里写死一个 bps 数就会与门禁那一侧说两套话——本模块的第一条纪律是
    「渲染与判定同源」。
    """
    if stop.cost_floor_mult <= 0:
        return ""
    return (f"另有一条**成本下界**：止损距离不得窄于 {stop.cost_floor_mult:g}× 当轮往返成本，"
            "更窄的提案会被系统抬到该距离（否则这一单只要被止损打掉，亏掉的价格幅度还不如"
            "手续费多）；若该下界已高于本会话的最大止损比例，则整条 ATR 覆盖回退为固定百分比。")


def format_sizing_for_prompt(plan: dict | None) -> str:
    """每轮决策 prompt 的仓位段；`plan` 为空或全部键缺席 ⇒ `""` ⇒ 整段省略（零回归）。"""
    if not plan:
        return ""
    parts: list[str] = []
    cap = plan.get("fCap")
    if cap is not None:
        detail = _cap_detail(plan)
        parts.append(f"系统解出的本轮上限占比≈{cap:.2f}{detail}")
    stop_pct, take_pct = plan.get("stopPct"), plan.get("takePct")
    if stop_pct is not None and take_pct is not None:
        parts.append(f"止损距离按 ATR 定标为 ±{stop_pct:.2%}（止盈 {take_pct:.2%}）")
    level = plan.get("brakeLevel")
    if level and level != "none":
        parts.append(f"回撤刹车：{describe_brake_level(level)}")
    dd = plan.get("drawdownPct")
    if dd is not None:
        parts.append(f"当前会话回撤 {dd:.1%}（含未实现盈亏）")
    if plan.get("exposureCapUnreachable"):
        parts.append("组合有效敞口已超上限且无法经缩减本笔达标，读数如实标注为不可达")
    if not parts:
        return ""
    return ("【仓位约束】" + "；".join(parts)
            + "。你给出的 sizeFraction 只会被调小、不会被调大。")


def describe_brake_level(level: str | None) -> str:
    """刹车档位中文化（后端侧唯一真源）；未知档位原样回退（绝不吞掉一个没见过的态）。"""
    if not level:
        return ""
    return _BRAKE_LABEL.get(level, level)


def _cap_detail(plan: dict) -> str:
    """上限的构成明细（两项都缺席 ⇒ 空串，不写「无」）。"""
    bits = []
    if plan.get("fVol") is not None:
        bits.append(f"波动率目标项 {plan['fVol']:.2f}")
    if plan.get("fKelly") is not None:
        bits.append(f"凯利项 {plan['fKelly']:.2f}")
    return f"（{' / '.join(bits)}）" if bits else ""
