"""外部平仓的**结算真值**匹配纯核心（ai-trade-fill-truth-and-effective-controls 轴 A）。

无 IO、无网络、无状态、**绝不抛**（全模块没有一处 `raise`）。存在的唯一理由：今天
`LiveExecutor._synthesize_external_close` 在侦测到「本地有仓、交易所没仓」时，会按**本地
`mark_px`** 合成一条平仓成交——代码自己的注释把这件事叫做「按 mark_px 捏造盈亏」。生产实测
20 条有交易所结算读数的 ai-trade run 上，本地账本合计 −42.84 USDT 而交易所可比口径合计
−56.80 USDT：**这套系统少报了 24.6% 的真实亏损**，而少报的那 18 笔恰恰是「确定性离场通道都是
正的」这条结论的正贡献最大项。本模块把「用哪个价落这条腿」的决定推迟到能拿到真值为止。

**它不是回写**（`reconcile.py` 模块 docstring / C-5 的成文纪律仍然成立）：一条腿仍然只被写一次，
只是写的时候用当时能拿到的最好真值。往返总费率同理——`resume._offline_exit_trade` 早已在做
「交易所往返总费 − 本地已记腿费」，本模块只是把同一条算法搬到实盘侧，并**保留那道先例里最
重要的守卫**：残差为负即判不自洽，宁可退回本地合成并说明，绝不静默夹成 0。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# `direction` 判据只在这两个取值上生效（fail-open，见 `match_settlement` 判据 2）。
_DIRECTIONS = ("long", "short")


@dataclass(frozen=True)
class SettlementQuery:
    """向 `positions-history` 提问的那一笔往返的本地断面。

    Attributes:
        inst_id: 标的。
        direction: 本地立场（`"long"` / `"short"`）——**只在交易所也给出可用方向时**才作判据。
        entry_ts: 本地开仓腿的秒级时间戳。
        detect_ts: 侦测到「交易所侧已平仓」的那一秒。
    """

    inst_id: str
    direction: str
    entry_ts: int
    detect_ts: int


@dataclass(frozen=True)
class SettlementFact:
    """一条被认领的交易所结算行（全部数值已由 `_normalize_position_history` 归一）。

    Attributes:
        pos_id: 交易所 `posId`（对一次持仓生命周期唯一，见不变式 I-3）。
        close_px: `closeAvgPx`——本轮要写进 `trades.price` 的那个价。
        gross_pnl_usd: `pnl`（正 = 盈利）；**不含**资金费与强平罚金。
        fee_usd: `fee`（已归一为「正 = 支出」），是这笔往返的**总**费率。
        funding_usd: `fundingFee`（正 = 支出）。**只进 explain.meta，绝不进本地账本。**
        liq_usd: `liqPenalty`（正 = 支出）。同上。
        close_ts: `uTime`（秒）。
    """

    pos_id: str
    close_px: float
    gross_pnl_usd: float
    fee_usd: float
    funding_usd: float
    liq_usd: float
    close_ts: int


def _num(value: object) -> float:
    """转有限浮点；非数值 / NaN / inf ⇒ `0.0`（结算读数里 NaN 比 0 危险得多）。"""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _int(value: object) -> int:
    """转整数秒；不可转 ⇒ `0`。"""
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _matches(row: dict, query: SettlementQuery, *, claimed: set[str], tol_s: int) -> bool:
    """单行是否命中（判据 1~6；顺序即优先级，抽出来是为了守圈复杂度红线）。"""
    if str((row or {}).get("instId") or "") != query.inst_id:
        return False
    # 判据 2：方向一致性 —— **fail-open，不是 fail-closed**（评审 RV-P0-4）。
    # `_normalize_position_history` 写的是 `str(r.get("direction") or "")`，缺字段即**空串**；而
    # OKX 的 `positions-history.direction` 在净持仓模式下并不保证是 `long/short`，本仓库的
    # `pos_mode` 又是从交易所账户配置取的。严格相等 + fail-closed 会让这类账户上**恒不命中**，
    # 而不命中的回落与今天逐字相同——于是整条轴 A 变成 100% 静默空转，`settlementCoverage.ratio`
    # 只会显示一个 0，没人能从中读出原因。仓库里已有反例：`resume` 的离线回读只按 `posId` 匹配、
    # **刻意不用 `direction`**。丢掉这条判据不放松安全性：判据 1 + 4/5 两个时间窗 + 判据 7 取最早，
    # 合起来已经把「上一笔往返」排除掉了，而「同一 instId 同一时间窗内同时存在多空两笔已结算持仓」
    # 在本执行器全程只持一笔仓的不变式下不可能发生。
    direction = str((row or {}).get("direction") or "")
    if direction in _DIRECTIONS and direction != query.direction:
        return False
    # 判据 3：`posId` 必须存在且未被认领过。**空串直接淘汰**——归一化对缺字段回落空串，而空串
    # 永远不在 `claimed` 里，只判 `not in claimed` 会把一条没有身份的结算行当成合法命中并认领。
    pos_id = str((row or {}).get("posId") or "")
    if not pos_id or pos_id in claimed:
        return False
    # 判据 4/5：两个时间窗。`cTime` / `uTime` 已由 `_normalize_position_history` 整除 1000 归一为
    # **秒**，故与 `detect_ts` / `entry_ts` 直接可比（评审曾怀疑此处毫秒/秒混用，核实后收回）。
    if _int((row or {}).get("uTime")) < query.detect_ts - int(tol_s):
        return False
    # 判据 5 同样 **fail-open**（与判据 2 一条理由）：`entry_ts <= 0` 表示本地根本不知道这笔仓是
    # 什么时候开的——那正是**接管来的孤儿持仓**的常态（`adopt_orphan` 不伪造开仓腿，台账里没有
    # 它）。此时若照常比对，`cTime <= 0 + tol` 对任何真实时间戳都为假 ⇒ 恒不命中 ⇒ 轴 A 对这类
    # 持仓 100% 静默空转，而它恰恰是最需要真值的那一类。「不知道」不等于「不匹配」。
    if query.entry_ts > 0 and _int((row or {}).get("cTime")) > query.entry_ts + int(tol_s):
        return False
    # 判据 6：**绝不用 0 价入账**（归一化对缺字段回落 0.0，那正是本轮在治的那类假数字）。
    return _num((row or {}).get("closeAvgPx")) > 0


def match_settlement(rows: list[dict], query: SettlementQuery, *,
                     claimed: set[str], tol_s: int) -> SettlementFact | None:
    """在 `positions-history` 的归一行里认领**这一笔**往返的结算（全仓唯一一处匹配判据）。

    实盘 / 单测共用本函数（回测不经 `LiveExecutor.reconcile`，轴 A 一行都不进回测链路）。

    Args:
        rows: `okx.trading.fetch_position_history` 的归一产物。
        query: 本地断面。
        claimed: 已被认领过的 `posId` 集合（调用方持有；一行结算只能被认领一次）。
        tol_s: 两个时间窗的容差秒数。

    Returns:
        命中的 `SettlementFact`；一行都不命中 ⇒ `None`（调用方按本地 `mark_px` 落账）。
        多行同时命中 ⇒ 取 `uTime` **最小**的那一行（最早的那次结算）。
    """
    picked = [row for row in (rows or [])
              if _matches(row or {}, query, claimed=claimed, tol_s=tol_s)]
    if not picked:
        return None
    row = min(picked, key=lambda r: _int(r.get("uTime")))
    return SettlementFact(
        pos_id=str(row.get("posId") or ""),
        close_px=_num(row.get("closeAvgPx")),
        gross_pnl_usd=_num(row.get("pnl")),
        fee_usd=_num(row.get("fee")),
        funding_usd=_num(row.get("fundingFee")),
        liq_usd=_num(row.get("liqPenalty")),
        close_ts=_int(row.get("uTime")))


def settlement_meta_wire(fact: SettlementFact | None, *, mark_px: float, waited_s: int,
                         booked_open_fee_usd: float, reject_reason: str | None = None) -> dict:
    """结算读数 → `explain.meta` 的加法键（**算不出即整键省略，绝不写 `null`**）。

    沿用 `trip_ledger.trip_meta_wire` 的成文纪律。装配点是 `session._inject_explain_meta`（在
    `exitSource` 打标之后 `setdefault`），**不是** `live_auto/explain.py`——那是 `live_auto` 与
    `ai_auto` 的共用模块，写在那里会同时改掉非 AI 链路的 `external_close` 形状，并造出第二个
    `explain.meta` 装配现场（评审 RV-P0-6）。

    Args:
        fact: 命中的结算行；`None` = 状态机跑过了但没拿到（超时 / 匹配不中 / 取数抛异常）。
        mark_px: 侦测那一刻的本地标记价。
        waited_s: 从侦测到落账等了多少秒。
        booked_open_fee_usd: 本地已记的开仓腿费（供费率残差核对）。
        reject_reason: 命中了却被判不自洽时的原因码（如 `"fee_residual_negative"`）。

    Returns:
        camelCase 加法键 dict。**注意本函数只描述「状态机真的跑过了」这两列**——总闸关或
        `settlement_fn is None` 时调用方根本不会走到这里，故 A-4 的 ⓐ 列（零加法键）是
        **构造性**成立的，不靠本函数判断。
    """
    out: dict = {"settlementSource": "exchange" if fact is not None else "local_mark",
                 "settlementWaitedS": int(max(0, waited_s))}
    if math.isfinite(mark_px) and mark_px > 0:
        out["markPxAtDetect"] = float(mark_px)
    if reject_reason:
        out["settlementRejectReason"] = str(reject_reason)
    if fact is None:
        return out
    out["settlementPosId"] = fact.pos_id
    out["settlementCloseTs"] = int(fact.close_ts)
    out["exchangeGrossPnlUsd"] = round(fact.gross_pnl_usd, 6)
    out["exchangeFeeUsd"] = round(fact.fee_usd, 6)
    # 资金费与强平罚金**只到这里为止**，绝不进本地账本：`reconcile.build_reconciliation` 的可比口径
    # `exchangeComparable = realized + funding + liq` 依赖本地不记这两项，记进来会让对账的差额恒等于
    # 它们本身（`G-A12`）。但也绝不能就此丢掉——它们正是「交易所毛额盈亏」与「用户在 OKX 上看到的
    # 那个数」之间的全部差值，没有它们这条腿的归因就缺一块，而这一段是这两个读数**唯一**的落点。
    # 命中结算时它们的值是**已知的**（含已知为 0），故与上面两行一样无条件写出。
    out["exchangeFundingUsd"] = round(fact.funding_usd, 6)
    out["exchangeLiqUsd"] = round(fact.liq_usd, 6)
    if math.isfinite(booked_open_fee_usd):
        out["bookedOpenFeeUsd"] = round(float(booked_open_fee_usd), 6)
    # 本轮最重要的一个观测量：它把「合成价到底差多少」从推断变成读数。两者都有才写。
    if math.isfinite(mark_px) and mark_px > 0 and fact.close_px > 0:
        out["settlementPriceGapBps"] = round((fact.close_px - mark_px) / mark_px * 1e4, 4)
    return out


def coverage_wire(exchange: int, local_mark: int) -> dict | None:
    """结算覆盖率 → `reconcile.settlementCoverage`；两者皆 0 ⇒ `None`（**整键省略**）。

    `N + M == 0` 时不写 `0` 也不写 `null`：「一次外部平仓都还没发生」与「发生了但一次都没拿到
    真值」是两件完全不同的事，用同一个 0 表示会让 `AC-1` 的分母失去意义。
    """
    total = int(exchange) + int(local_mark)
    if total <= 0:
        return None
    return {"exchange": int(exchange), "localMark": int(local_mark),
            "ratio": round(int(exchange) / total, 4)}
