"""决策轮 ⇄ 成交 ⇄ 订单 ⇄ 往返结果的**纯函数**关联（ai-trade-audit-journal §2.4.2）。

日志层**不复制** `trades` / `live_order_audit`：再存一份副本就会产生「两份真值、其中一份悄悄
过期」这个本仓库反复付过代价的形状。结果一律读时关联，并**如实标注置信度**。

四步逐步降级，每一步都有自己的真源：

1. **精确锚点** `clOrdId`（决策行的 `fill_ref_json.legs[].clOrdId` ↔ 成交行
   `explain_json.meta.clOrdId`）⇒ `exact`。
2. **启发式**（同一 run 内 `side` 相同、时间差 ≤ 90s、数量几乎相等）⇒ `heuristic`；
   **必须唯一命中**——两条及以上一律判 `none`，绝不「挑一个最近的」。
3. **未成交轮** ⇒ `none` + `noFillReason`。判据是 `executed.traded is False` **且**
   `fill_ref_json.legs` 为空——「`executed` 说没成交」不等于「这一轮没有成交」：`_post_round`
   里的利润保护会在同一轮再平一笔，而那笔不在 `executed` 里。
4. **往返结果**：开仓腿之后、同一 run 内**按 `trades.id` 升序**的下一条 `close`。

⚠️ 第 4 步的排序键必须是 `trades.id`，**绝不能是 `trades.ts`**。AI 自动交易的 `trades.ts` 由
**两套时钟**写入（K 线轮 = bar 开盘秒 / 定时唤醒轮与盯市轮 = 墙钟），按 `ts` 排序会破坏真实的
开→平交替。这条已在 `db/ai_auto_repositories._pair_round_trips` 的 docstring 里被完整记录
（「实测生产库按 id 配对 161 笔平仓零孤儿」），本模块逐字沿用同一条判据。
"""
from __future__ import annotations

# 启发式匹配的时间窗（秒）与数量容差。两者都刻意收得很紧：一次误配会让后训练样本的**标签**
# 是错的，而那种错误在训练完成之前不会有任何征兆。
HEURISTIC_TIME_WINDOW_S = 90
HEURISTIC_QTY_TOL = 1e-9

EXACT = "exact"
HEURISTIC = "heuristic"
NONE = "none"

# 置信度的强弱序（导出侧的 `minLinkConfidence` 按它比较，前端徽标也按它排）。
CONFIDENCE_ORDER = {NONE: 0, HEURISTIC: 1, EXACT: 2}

_OPEN_SIDES = ("long", "short")


def _legs(decision: dict) -> list[dict]:
    ref = decision.get("fillRef") or {}
    return [leg for leg in (ref.get("legs") or []) if isinstance(leg, dict)]


def _anchor_cl_ord_id(decision: dict) -> str:
    """决策行的精确锚点：最后一条带 `clOrdId` 的腿；没有即空串。"""
    for leg in reversed(_legs(decision)):
        if leg.get("clOrdId"):
            return str(leg["clOrdId"])
    return str(decision.get("clOrdId") or "")


def _trade_cl_ord_id(trade: dict) -> str:
    meta = trade.get("meta") or {}
    return str(meta.get("clOrdId") or "")


def link_one(decision: dict, trades: list[dict]) -> dict:
    """把一条决策行关联到它的成交、订单与往返结果。

    Args:
        decision: `db.ai_journal_repositories.decision_full()` 的产物。
        trades: 同一 `run_id` 的全部成交腿，**已按 `trades.id` 升序**。

    Returns:
        `{"linkConfidence", "fill", "trip", "noFillReason", "fillSource"}`——
        取不到的键一律为 `None`（**绝不补 0**，也绝不用一个空 dict 冒充「查到了但是空的」）。
    """
    out = {"linkConfidence": NONE, "fill": None, "trip": None,
           "noFillReason": None, "fillSource": None}
    legs = _legs(decision)
    executed = bool((decision.get("fillRef") or {}).get("traded"))
    if not executed and not legs:
        # 第 3 步：本轮真的什么都没发生。原因取 `executed.reason`（`LIVE_NO_POSITION` /
        # `open_funnel_*` / `gate_veto` …），没有原因也如实留空——别编一个。
        out["noFillReason"] = ((decision.get("fillRef") or {}).get("reason")
                               or decision.get("degradeReason"))
        return out
    # 只要 `legs` 非空，本轮就发生过成交——哪怕 `executed.traded` 是 False（利润保护在
    # `_post_round` 里平掉的那一笔就是这个形状）。按最后一条腿去锚定。
    if legs:
        out["fillSource"] = legs[-1].get("source") or "round"
    anchor = _anchor_cl_ord_id(decision)
    fill = _match_exact(anchor, trades) if anchor else None
    if fill is not None:
        out["linkConfidence"] = EXACT
    else:
        fill = _match_heuristic(decision, legs, trades)
        if fill is not None:
            out["linkConfidence"] = HEURISTIC
    if fill is None:
        return out
    out["fill"] = fill
    out["trip"] = build_trip(fill, trades)
    return out


def _match_exact(cl_ord_id: str, trades: list[dict]) -> dict | None:
    """按 `clOrdId` 精确命中。**多条命中一律判失败**——同一单号对两条成交是数据自相矛盾。"""
    hits = [t for t in trades if _trade_cl_ord_id(t) == cl_ord_id]
    return hits[-1] if len(hits) == 1 else None


def _match_heuristic(decision: dict, legs: list[dict], trades: list[dict]) -> dict | None:
    """时间 + 方向 + 数量的三重近似匹配，**必须唯一命中**，否则返回 `None`。

    「挑一个最近的」在这里是被禁止的：一次误配换来的不是「多一条样本」，而是「一条标签是错的
    样本」，而它在训练完成之前不会有任何征兆。
    """
    if not legs:
        return None
    leg = legs[-1]
    side, qty = leg.get("side"), leg.get("sz")
    ts = int(decision.get("ts") or 0)
    if side is None or qty is None:
        return None
    hits = [t for t in trades
            if t.get("side") == side
            and abs(int(t.get("ts") or 0) - ts) <= HEURISTIC_TIME_WINDOW_S
            and abs(float(t.get("qty") or 0.0) - float(qty)) <= HEURISTIC_QTY_TOL
            and not _trade_cl_ord_id(t)]
    return hits[0] if len(hits) == 1 else None


def build_trip(fill: dict, trades: list[dict]) -> dict | None:
    """往返结果：开仓腿之后**按 `id` 升序**的下一条 `close` 腿。

    标签一律从平仓腿取，且 **缺键即缺席、绝不补 0**——`trip_ledger.trip_meta_wire` 的约束 6
    原文就是「键一律『算不出即省略』」，在这里补一个 0 等于把「算不出来」写成「等于零」。

    Args:
        fill: 已关联到的那条成交腿。
        trades: 同一 run 的全部腿（按 `id` 升序）。

    Returns:
        `{"exitId","exitTs","pnl","fee",…}`；`fill` 本身就是平仓腿 ⇒ 直接以它结算；
        找不到平仓腿（仓位仍持有中）⇒ `None`。
    """
    exit_leg = fill if fill.get("side") == "close" else None
    if exit_leg is None:
        if fill.get("side") not in _OPEN_SIDES:
            return None
        fill_id = int(fill.get("id") or 0)
        exit_leg = next((t for t in trades
                         if int(t.get("id") or 0) > fill_id and t.get("side") == "close"), None)
    if exit_leg is None:
        return None
    trip = {"exitId": exit_leg.get("id"), "exitTs": exit_leg.get("ts"),
            "exitPx": exit_leg.get("price"), "pnl": exit_leg.get("pnl"),
            "fee": exit_leg.get("fee"), "exitReason": exit_leg.get("reason")}
    meta = exit_leg.get("meta") or {}
    for key in ("realizedBps", "rMultiple", "roundTripFeeUsd", "exitSource", "holdSeconds",
                "costBasis", "grossBps", "costBps"):
        if meta.get(key) is not None:
            trip[key] = meta[key]
    return trip


def link_decisions_to_fills(decisions: list[dict],
                            trades_by_run: dict[int, list[dict]]) -> list[dict]:
    """批量关联（列表页与导出共用）。返回与入参**同序**的关联结果。"""
    return [link_one(row, trades_by_run.get(int(row.get("runId") or 0), []))
            for row in decisions]


def resolve_min_link_confidence(requested: str | None, *, has_client_order_id: bool) -> dict:
    """`minLinkConfidence=auto` 的**服务端唯一**解出点（评审 RP1-6）。

    默认值不能是一个恒定的 `exact`：AI 自动交易支持 `creds is None` 的纸面 / 无凭证会话，
    那种会话在**结构上**没有交易所单号，也不会有任何 `live_order_audit` 行——恒定 `exact`
    会让它导出 0 条样本，而界面上说不出为什么。

    Args:
        requested: 用户显式指定的档位；`None` / `"auto"` ⇒ 自适应。
        has_client_order_id: 该 scope 内是否**存在**任一 `cl_ord_id` 非空的决策行。

    Returns:
        `{"value", "reason", "auto"}`——`value` 随预检与 manifest 一起下发，
        前端**照抄不重算**（`resolvedMinLinkConfidence`）。
    """
    if requested and requested != "auto":
        return {"value": requested, "reason": "explicit", "auto": False}
    if has_client_order_id:
        return {"value": EXACT, "reason": "client_order_id_present", "auto": True}
    return {"value": HEURISTIC, "reason": "no_client_order_id_in_scope", "auto": True}


def meets_confidence(actual: str, minimum: str) -> bool:
    """`actual` 是否达到 `minimum` 档（强弱序见 `CONFIDENCE_ORDER`）。"""
    return CONFIDENCE_ORDER.get(actual, 0) >= CONFIDENCE_ORDER.get(minimum, 0)
