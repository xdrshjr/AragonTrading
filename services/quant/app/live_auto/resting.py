"""挂单（resting order）生命周期的**纯函数**核心（ai-trade-limit-orders §2.3 / §2.6）。

本模块无 IO、无网络、无状态：只承载 `RestingOrder` 值对象与围绕它的映射 / 判定纯函数，使
`LiveExecutor._poll_resting` 的状态分派不至于破 CLAUDE.md 的「单函数 ≤50 行 / 圈复杂度 ≤10」，
也使 TTL、OKX 状态归一、价带门禁三者的单测**不必构造 executor 或 decider**。

为何需要一个生命周期状态机（本特性全部复杂度的来源）：`LiveExecutor` 是**乐观成交模型**——发出
市价单后立即假定已成交（`executor.py:189-191` 写死 `projected`/`entry_price`）。限价单**可能不成交**，
该假设随之破裂；更危险的是 `_on_reconcile_ok` 每 5 秒见「自管有仓、交易所无仓」就合成 `external_close`
假成交并捏造盈亏。故限价路径**绝不**沿用乐观模型：挂单期间 `projected` 保持挂单前的值（不变式
I-2a/I-2b），成交与否一律以 OKX `GET /trade/order` 的权威 `state` 为准。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

# OKX 订单 state → 生命周期归一态（§2.3）。刻意用查找表而非 if 阶梯：新增 state 只加一行，
# 且「未知 state 一律 unknown（= 保留挂单、下轮再判）」这条兜底在结构上不可能被漏写。
_STATE_MAP = {
    "live": "live",
    "partially_filled": "partial",
    "filled": "filled",
    "canceled": "canceled",
    "mmp_canceled": "canceled",
}

# 限价意图校验的降级原因（均属 LLM 瞬时失误 → 决策器侧归入可重试集合，§2.6）。
DEGRADE_MISSING_LIMIT_PRICE = "missing_limit_price"
DEGRADE_LIMIT_PRICE_OUT_OF_BAND = "limit_price_out_of_band"
DEGRADE_INVALID_ORDER_TYPE = "invalid_order_type"

_ORDER_TYPES = ("market", "limit")


@dataclass(frozen=True)
class RestingOrder:
    """会话当前挂着的那**一笔**未成交委托（不变式 I-1：单会话至多一笔）。

    `frozen=True` 是刻意的——不可变性正是本类易测的来源。推进 `adopted_sz` 请用
    `adopt_fill_delta` 生成新值对象，**不要**改成可变 dataclass。
    """

    cl_ord_id: str
    ord_id: str
    intent: str          # "open" | "close"
    side: str            # open: long|short；close: 被平方向
    px: float            # 委托价（权威 tickSz 对齐由 build_order_payload 完成）
    sz: float            # 已按 lotSz 对齐的张数
    strength: float      # open 用（成交后回填 entry_position_pct）
    reason: str
    created_ts: int
    expire_ts: int       # created_ts + ai_trade_limit_order_ttl_s
    adopted_sz: float = 0.0   # 已采纳的成交张数；部分成交按**增量**采纳，防重复计入（R-19）


def build_resting(*, cl_ord_id: str, ord_id: str, intent: str, side: str, px: float,
                  sz: float, strength: float, reason: str, now_ts: int,
                  ttl_s: float) -> RestingOrder:
    """装配一笔新挂单（TTL 由 `now_ts + ttl_s` 派生，绝不在别处重复算，单一真源）。"""
    return RestingOrder(
        cl_ord_id=cl_ord_id, ord_id=ord_id, intent=intent, side=side, px=float(px),
        sz=float(sz), strength=float(strength), reason=reason, created_ts=int(now_ts),
        expire_ts=int(now_ts + max(0.0, float(ttl_s))))


def classify_state(state: str) -> str:
    """OKX 订单 `state` → 归一生命周期态。

    Args:
        state: `GET /trade/order` 回读的 `state`（live/partially_filled/filled/canceled/mmp_canceled）。

    Returns:
        `"live"|"partial"|"filled"|"canceled"|"unknown"`。**未知取值一律 `"unknown"`**——它的语义是
        「状态未知，保留挂单、下轮再判」，绝不猜成任何一个会触发动作的态（兜底方向必须是「不动」）。
    """
    return _STATE_MAP.get(str(state or ""), "unknown")


def is_expired(order: RestingOrder, now_ts: int) -> bool:
    """该挂单是否已到 TTL（到期由 `_poll_resting` 撤单，是 Agent 主动 `cancel_order` 之外的兜底）。"""
    return int(now_ts) >= order.expire_ts


def fill_delta(order: RestingOrder, acc_fill_sz: float) -> float:
    """本轮**新增**成交张数 = `accFillSz - adopted_sz`（R-19 防重复采纳）。

    `accFillSz` 是 OKX 的**累计**值，逐轮全量采纳会把持仓越加越多。负值（异常回读）夹到 0。
    """
    return max(0.0, float(acc_fill_sz or 0.0) - order.adopted_sz)


def adopt_fill_delta(order: RestingOrder, acc_fill_sz: float) -> RestingOrder:
    """推进已采纳张数到 `accFillSz`（frozen dataclass → 返回新值对象）。"""
    return replace(order, adopted_sz=max(order.adopted_sz, float(acc_fill_sz or 0.0)))


def is_same_intent(order: RestingOrder, *, intent: str, side: str, px: float) -> bool:
    """新意图是否与当前挂单**逐字同向同价**（P2-3 幂等：同则不撤不挂，保住排队优先级）。

    maker 单的价值恰在排队——Agent 每轮重复同一判断都撤挂一次会白白丢掉它。价格按原始委托价
    比较（本执行器不持有 `tickSz`，权威对齐在 `build_order_payload`；见 spec 文末 P2-1 拍板）。
    """
    return (order.intent == intent and order.side == side
            and float(px) == float(order.px))


def resting_payload(order: RestingOrder | None, now_ts: int) -> dict | None:
    """挂单 → 决策 prompt 的只读上下文段（§4.2）；无挂单返回 None（该段整体省略）。

    Agent 看不见自己的挂单就无法就它做任何决策——**这是 `cancel_order` 可用的前提**。
    """
    if order is None:
        return None
    return {
        "intent": order.intent,
        "side": order.side,
        "px": order.px,
        "sz": order.sz,
        "ageS": max(0, int(now_ts) - order.created_ts),
        "expiresInS": max(0, order.expire_ts - int(now_ts)),
        "filledSz": order.adopted_sz,
    }


def _positive_float(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")) or n <= 0:   # NaN / inf / 非正
        return None
    return n


def validate_limit_intent(raw: dict, last_px: float | None,
                          max_dev: float) -> tuple[str, float | None, str | None]:
    """校验 LLM 给出的 `orderType`/`limitPrice`（**纯函数**，§2.6 / §八.11）。

    价带门禁必须在决策器的 `_map_decision` 内调用（而非下游 `order_plan.py`）——重试循环在
    `decider.decide` 里且**只认 `_map_decision` 返回的 degrade**，门禁若落在下游，「重试一次极可能
    给出合理价」的承诺会**静默失效**（P1-5/R-15）。抽为纯函数则 `_map_decision` 只需一次调用 + 一个
    分支，不破其 50 行 / 复杂度 10 红线，且本函数的单测不必构造 `AiTradeDecider`。

    Args:
        raw: LLM 工具入参原始 dict。
        last_px: 本轮最新价（取自决策 payload 的 `lastPx`，**与喂给 prompt 的价天然同源**）。
        max_dev: 本地价带上限（`|limitPx-lastPx|/lastPx` 超此值即降级）。

    Returns:
        `(ord_type, limit_px, degrade_reason)`。`degrade_reason` 非 None ⇒ 调用方须降级 hold；
        此时 `ord_type` 恒为 `"market"`、`limit_px` 恒为 None（降级路径绝不携带半截限价意图）。
        `last_px` 缺失或 ≤0 时限价单**降级**而非放行——绝不跳过门禁。
    """
    ord_type = str(raw.get("orderType") or "market")
    if ord_type not in _ORDER_TYPES:
        return "market", None, DEGRADE_INVALID_ORDER_TYPE
    if ord_type == "market":
        return "market", None, None
    limit_px = _positive_float(raw.get("limitPrice"))
    if limit_px is None:
        return "market", None, DEGRADE_MISSING_LIMIT_PRICE
    ref = _positive_float(last_px)
    if ref is None:
        return "market", None, DEGRADE_LIMIT_PRICE_OUT_OF_BAND
    if abs(limit_px - ref) / ref > max(0.0, float(max_dev)):
        return "market", None, DEGRADE_LIMIT_PRICE_OUT_OF_BAND
    return "limit", limit_px, None
