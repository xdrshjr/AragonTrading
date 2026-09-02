"""孤儿持仓侦测的**纯核心**（ai-trade-fill-truth-and-effective-controls 轴 B）。

无 IO、无状态、绝不抛。它补上的是 `LiveExecutor._on_reconcile_ok` 里一个**结构性缺失的对称
分支**：今天全文只有 `projected != "flat" and abs_pos <= 0`（本地有仓、交易所没仓）这一个方向，
**没有 `projected == "flat" and abs_pos > 0`**（交易所有仓、本地不知道）。后果是一笔因「撤单与
成交竞态」「限价单部分成交」「用户手工下单」而出现在交易所的持仓，对整条自动交易链完全不可见
——不受持仓周期闸管、不受利润保护管、不受活性看门狗管，它只会在对账的 `coverageMismatch` 上
留下一个数字。生产实测已经出现过两次（run 1601 / run 1613）。

**本层只写状态与事件，绝不产生任何交易所写请求**（不变式 I-4）。接管必须是用户的一次显式点击
——「自动接管一笔来路不明的真实持仓」是本轮能想到的第二坏的事，仅次于在它之上再叠一笔。

判定方向是 **fail-closed**：任一条不确定即 `"none"`。一次误报等于该标的停止交易，而误报会把
真报一起废掉（`discipline_view.py` 的成文纪律）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# 三态。`"pending"` 是「看见了但还没连续够轮数」，它**不推事件、不封锁开仓**。
STATE_NONE = "none"
STATE_PENDING = "pending"
STATE_CONFIRMED = "confirmed"

# 开仓被孤儿持仓拦下时的降级码（`open_funnel._DEGRADE_PREFIXES` 与前端标签表共用这个前缀）。
DEGRADE_ORPHAN_BLOCKED = "orphan_position_blocked"


@dataclass(frozen=True)
class OrphanFacts:
    """一轮盯市里与「交易所有仓、本地不知道」有关的全部事实。

    Attributes:
        projected: 本地自管立场（`"flat"` / `"long"` / `"short"`）。
        abs_pos: 交易所侧该标的的净持仓绝对值。
        settling: 是否正处于轴 A 的 `settling` 中间态。
        fill_grace: 既有的「刚采纳完开仓成交」一轮宽限。
        streak: 已连续判定成立的轮数（纯核心不持状态，由 `LiveExecutor` 持有并传入）。
    """

    projected: str
    abs_pos: float
    settling: bool
    fill_grace: bool
    streak: int


def classify_orphan(facts: OrphanFacts, *, confirm_rounds: int) -> str:
    """三态判定；判据顺序即优先级，任一条不确定即 `"none"`（fail-closed）。

    判据 1 是**反向门禁的落点**：`settling` 与 `fill_grace` 两种状态下「交易所有仓、本地 flat」
    恰恰是**正常的中间态**——前者是轴 A 刻意冻结立场等结算的那 90 秒，后者是刚成交而持仓读数还
    没跟上的那一轮。轴 A 与轴 B 在这里必须互斥，否则两条新轴会互相误报。

    `confirm_rounds` 是 `_fill_grace` 的**镜像**：那一条防「刚开的仓交易所还没跟上」，这一条防
    「刚下的开仓单成交了、本地还没接管」——两者是同一枚硬币的两面。

    Args:
        facts: 本轮事实。
        confirm_rounds: 连续多少轮成立才转 `confirmed`（≥1）。

    Returns:
        `"none"` | `"pending"` | `"confirmed"`。
    """
    if facts.settling or facts.fill_grace:
        return STATE_NONE
    if facts.projected != "flat":
        return STATE_NONE          # 本地有仓 ⇒ 这是轴 A 那一侧的事，本层不插手
    if not isinstance(facts.abs_pos, (int, float)) or not math.isfinite(facts.abs_pos):
        return STATE_NONE
    if facts.abs_pos <= 0:
        return STATE_NONE
    streak = facts.streak if isinstance(facts.streak, int) and facts.streak >= 0 else 0
    return STATE_CONFIRMED if streak + 1 >= max(1, int(confirm_rounds)) else STATE_PENDING


def orphan_wire(state: str, *, qty: float, avg_px: float, upl_usd: float,
                since_ts: int) -> dict | None:
    """`status.member.orphan` 的加法键；`state == "none"` ⇒ `None`（**整键省略**）。

    Args:
        state: `classify_orphan` 的产物。
        qty: 交易所侧净持仓绝对值（张）。
        avg_px: 交易所侧开仓均价。
        upl_usd: 交易所侧未实现盈亏。
        since_ts: 第一次判定成立的那一秒。

    Returns:
        camelCase 只读 dict，或 `None`。
    """
    if state not in (STATE_PENDING, STATE_CONFIRMED):
        return None
    out: dict = {"state": state, "qty": _num(qty), "sinceTs": int(since_ts)}
    if _num(avg_px) > 0:
        out["avgPx"] = _num(avg_px)
    # 无条件写：`_num` 已经把 NaN / inf / 非数值收敛成 `0.0`，再套一层 `math.isfinite` 是一个
    # 恒真的守卫（读起来像在挡什么，实际什么都挡不住）。浮盈算不出时按 0 呈现是本层的既定口径
    # ——`qty` / `sinceTs` 也是这么来的。
    out["uplUsd"] = round(_num(upl_usd), 6)
    return out


def orphan_message(view: dict | None, *, inst_id: str, block_open: bool) -> str:
    """`AI_TRADE_ORPHAN_POSITION` 事件与横幅共用的中文文案。

    ⚠️ **不得出现字面 `**`**：后端文案在 JSX 里是纯文本节点，Markdown 粗体标记会原样显示成星号。
    这是本仓库第 5 次登记同一条纪律，门禁 `G-B6`（前后端双侧）与 `G-X3`（仓库级源码扫描）同守。

    Args:
        view: `orphan_wire` 的产物；`None` ⇒ 回落一句不带读数的说明。
        inst_id: 标的。
        block_open: 是否已连带封锁该标的的自动开仓。

    Returns:
        一句话现状 + 三条能真的点下去的补救。
    """
    head = f"{inst_id} 在交易所侧存在本会话不知道的持仓"
    if isinstance(view, dict) and _num(view.get("qty")) > 0:
        head += f"：{_num(view['qty']):g} 张"
        if view.get("avgPx"):
            head += f"，开仓均价 {view['avgPx']:g}"
        if view.get("uplUsd") is not None:
            head += f"，当前浮盈 {view['uplUsd']:+.4f} USDT"
    blocked = ("为避免在一笔无人看管的持仓上再叠一笔，该标的的自动开仓已暂停。"
               if block_open else "")
    return (f"{head}。它不受持仓周期、利润保护与活性看门狗的任何管辖，"
            f"止盈止损只由交易所侧已挂的委托承担。{blocked}"
            "可选处置：① 接管这笔持仓（纳入本会话账本与风控）；"
            "② 到实盘面板手动平掉；③ 停止该标的的自动交易。")


def _num(value: object) -> float:
    """转有限浮点；非数值 / NaN / inf ⇒ `0.0`。"""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0
