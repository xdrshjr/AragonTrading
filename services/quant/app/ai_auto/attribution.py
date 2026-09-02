"""滚动往返归因（ai-trade-cost-accounting-and-edge-gate §3.8，D-15）。

把「每一笔往返到底赚了多少、其中多少被成本吃掉、门禁拦了多少轮、有多少轮真的落到成交」记成可读
数字。零新表、零迁移：内存环形缓冲（容量 200），随会话终结经既有 `metrics_json` 落库。

一条口径钉子：`grossPnlUsd` 与 `netPnlUsd` 的关系取决于 A0-2 开关。`ai_trade_net_pnl_enabled=True`
时 `trade["pnl"]` **已经是净额**，此时毛额须由 `pnl + fee` 反推；开关关闭时 `trade["pnl"]` 是毛额，
净额由 `pnl − fee` 推。两者搞反会让归因表在切换口径的那一刻整体偏移一个往返成本——而那恰恰是本
特性要修正的那类误差。
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from .trip_ledger import (
    PENDING_ESTIMATE_MAX,
    OpenSnapshot,
    RoundEstimate,
    SettleContext,
    SettledTrip,
    merge_open_leg,
    open_snapshot,
    settle,
    trip_meta_wire,
)

_CAPACITY = 200
_ROUND_USD = 6
_ROUND_RATIO = 4

# ---- 轴 F（loss-and-liveness-hardening §4.6）：退出裁量权的**只读结论**，无任何门禁 ----
# `ExitSourceCard` 的文档注释里已经记着与本轮 §2.2 完全一致的观察（「16 次往返、15 次 Agent 主动
# 平仓、唯一一次保护层平仓反而净赚 1.87 USDT」），而卡片同时明确声明 `exitMix` **不参与任何判定**
# ——理由是「把它做成门槛会诱导『故意不平仓等保护层』」。**那条理由是对的，本轮不推翻它。**
# 但「不做硬门槛」不等于「什么都不做」：这里产出一句**数据化**的结论，让用户自己去按那个开关。
DISCRETION_LEVEL_INSUFFICIENT = "insufficient"
DISCRETION_LEVEL_ADVERSE = "adverse"
DISCRETION_LEVEL_NEUTRAL = "neutral"


def exit_discretion_verdict(rows: list[dict] | None, *, min_samples: int,
                            discretionary: tuple[str, ...],
                            deterministic: tuple[str, ...],
                            labels: dict[str, str] | None = None) -> dict | None:
    """退出裁量权结论（**纯读数**）；`min_samples <= 0` ⇒ `None` ⇒ 整键缺席 ⇒ 逐字回退。

    判定只有三档，且每一档都必须能被 `rows` 里的数字逐字复算：

    - `insufficient`：模型自主平仓的样本数还不到门槛 ⇒ 如实写「样本 M/N，结论未生效」。
    - `adverse`：样本够了、模型自主平仓合计为**负**、且至少一条确定性来源合计为**正**。
    - `neutral`：样本够了但上面那两个条件不同时成立 —— 不下任何结论。

    文案里**不得出现「已校准」「经回测验证」**这类措辞（`G-F2` 源码正则门禁）：本轮的阈值一个都
    没有被回测校准过，说它校准过就是一句会被用户当依据的假话。

    Args:
        rows: `ExitLedger.stats()["bySource"]` 的行（`source` / `count` / `netUsd` / …）。
        min_samples: 出结论所需的最小自主平仓往返数；`0` ⇒ 恒不出结论。
        discretionary: 属于「模型自由裁量」的来源枚举（调用方从 `EXIT_SOURCES` 派生，不手抄）。
        deterministic: 属于「确定性通道」的来源枚举（同上）。
        labels: 来源枚举 → 中文标签（调用方从 `acceptance_text.EXIT_SOURCE_LABELS` 取，
            那是这张表的**唯一真源**）。**结论是给人读的**：缺了它 `note` 里会出现一个裸英文
            枚举（`profitGuard`），那是本仓库明令禁止的形态。缺省 ⇒ 原样用枚举值（只在单测里发生）。

    Returns:
        `{level, agentTrips, agentNetUsd, minSamples, bestDeterministic, note}`；
        `bestDeterministic` 在没有任何正的确定性来源时为 `None`。
    """
    if int(min_samples) <= 0:
        return None
    by_source = {str(row.get("source") or ""): row for row in (rows or [])}
    agent_trips = sum(int(by_source.get(key, {}).get("count") or 0) for key in discretionary)
    agent_net = sum(_finite(by_source.get(key, {}).get("netUsd")) for key in discretionary)
    best = None
    for key in deterministic:
        row = by_source.get(key)
        if row is None or _finite(row.get("netUsd")) <= 0:
            continue
        if best is None or _finite(row.get("netUsd")) > best["netUsd"]:
            best = {"source": key, "label": (labels or {}).get(key, key),
                    "trips": int(row.get("count") or 0),
                    "netUsd": round(_finite(row.get("netUsd")), _ROUND_USD)}
    agent_net = round(agent_net, _ROUND_USD)
    if agent_trips < int(min_samples):
        return {"level": DISCRETION_LEVEL_INSUFFICIENT, "agentTrips": agent_trips,
                "agentNetUsd": agent_net, "minSamples": int(min_samples),
                "bestDeterministic": best,
                "note": f"样本 {agent_trips}/{int(min_samples)}，结论未生效。"}
    if agent_net < 0 and best is not None:
        return {"level": DISCRETION_LEVEL_ADVERSE, "agentTrips": agent_trips,
                "agentNetUsd": agent_net, "minSamples": int(min_samples),
                "bestDeterministic": best,
                "note": (f"模型自主平仓 {agent_trips} 笔合计 {agent_net:+.2f} USDT；"
                         f"{best['label']}平仓 {best['trips']} 笔合计 "
                         f"{best['netUsd']:+.2f} USDT。")}
    return {"level": DISCRETION_LEVEL_NEUTRAL, "agentTrips": agent_trips,
            "agentNetUsd": agent_net, "minSamples": int(min_samples),
            "bestDeterministic": best,
            "note": (f"模型自主平仓 {agent_trips} 笔合计 {agent_net:+.2f} USDT，"
                     "未出现「自主通道为负而确定性通道为正」的分化。")}


@dataclass(frozen=True)
class RoundTrip:
    """一笔已完成往返的归因样本（只记平仓成交；开仓腿的费率已并入其平仓样本的净额口径）。"""

    ts: int
    side: str
    ord_type: str
    gross_pnl_usd: float
    fee_usd: float
    net_pnl_usd: float
    notional_usd: float
    cost_bps: float | None
    edge_bps: float | None


class AttributionLedger:
    """会话级滚动归因：往返样本 + 门禁计数 + 决策转化率。纯内存、绝不抛。"""

    def __init__(self, capacity: int = _CAPACITY, *, net_mode: bool = False,
                 ct_val: float = 0.0) -> None:
        # A0-2 开关是会话级静态量，故在构造期读一次（调用点不必逐笔重复传，见 spec §十二 I-3）。
        self.net_mode = bool(net_mode)
        # 合约乘数：名义额是 `价 × 张数 × ctVal`，漏掉它会让 `notionalUsd` 差一个合约乘数量级
        # （BTC-USDT-SWAP 的 ctVal=0.01 ⇒ 高估 100 倍）。未知合约（未装配账本）⇒ 0 ⇒ 不报名义额。
        self.ct_val = float(ct_val or 0.0)
        self._trips: deque[RoundTrip] = deque(maxlen=capacity)
        # [P1-1] **全程累计**口径的四个只增累加器，与上面那个 200 容量环形缓冲**并存**。
        # 理由：环形缓冲让 `stats()["cost"]` 的四项天然是滚动窗口口径，而周期验收卡的持仓样本与
        # 资金费账本都是全程累计——超过 200 笔往返的会话（恰恰就是验收卡要抓的高换手失败态，
        # 按 3 分钟唤醒推算一周就能跑过 200 笔）第 4、5 行会悄悄换成另一个时间窗，而卡片上没有
        # 任何东西提示这件事。两套数用途分开且**都标注口径**（R-13）。
        self._lifetime = {"trips": 0, "gross": 0.0, "fee": 0.0, "net": 0.0,
                          "absGross": 0.0}
        self._close_samples = 0           # 平仓成交样本数（closeFeeCoverage 的分母）
        self._close_with_fee = 0          # 其中 fee != 0 的条数
        self._gate = {"pass": 0, "veto": 0, "unavailable": 0}
        self._rounds = 0
        self._traded_rounds = 0
        # 最近一次门禁读数（成本模型 / 粗估 edge）；门禁空转的会话恒为 None。
        self._last_cost_bps: float | None = None
        self._last_edge_bps: float | None = None
        # ---- 轴 A（realized-edge-calibration）：估计 → 开仓断面 → 已结算往返 ----
        # 三者与上面那个 `_trips` **并存不合并**：`_trips` 是已发布 `stats()["cost"]` 的口径来源，
        # 动它就是追溯改写已发布 KPI。
        self._round_estimate: RoundEstimate | None = None            # 轮级，轮末清零
        self._pending_estimates: dict[str, RoundEstimate] = {}       # [P0-2] 按 clOrdId 的待成交槽
        self._open: OpenSnapshot | None = None                       # 持仓周期级
        self._settled: deque[SettledTrip] = deque(maxlen=capacity)

    # ---------- 轴 A：估计的三个槽位（D-3 的错绑修复） ----------
    def note_estimate(self, est: RoundEstimate | None) -> None:
        """冻结本轮受门禁约束的开仓估计（**轮级**；观望 / 平仓轮拿不到 `out` ⇒ 恒 None）。"""
        self._round_estimate = est

    def claim_estimate(self, cl_ord_id: str | None) -> None:
        """[P0-2] 下单那一刻把轮级估计移进**待成交槽**（按 `clOrdId`）。

        `ai_trade_limit_orders_enabled` **默认 True**，限价开仓单会 resting 若干轮，其采纳成交由
        盯市 tick 发出——那时轮级槽早已被 `_run_decision_round` 的 `finally` 清零。只有轮级槽的话，
        **默认订单类型的每一笔开仓都拿不到估计**，轴 C 的问一与问三永远是空表。
        容量上限 `PENDING_ESTIMATE_MAX`，超量按 FIFO 丢弃（挂单额度只有一个，正常只会有 1 项）。
        """
        if not cl_ord_id or self._round_estimate is None:
            return
        while len(self._pending_estimates) >= PENDING_ESTIMATE_MAX:
            self._pending_estimates.pop(next(iter(self._pending_estimates)))
        self._pending_estimates[str(cl_ord_id)] = self._round_estimate

    def end_round(self) -> None:
        """清零轮级槽（D-3）：一次被否的开仓意图绝不可改绑到下一笔真实往返上。"""
        self._round_estimate = None

    def _take_estimate(self, trade: dict) -> RoundEstimate | None:
        """开仓成交认领估计：**先按 `clOrdId`**（限价跨轮），认领不到再回退轮级槽（市价恒走回退）。"""
        cl_ord_id = trade.get("clOrdId")
        if cl_ord_id and str(cl_ord_id) in self._pending_estimates:
            return self._pending_estimates.pop(str(cl_ord_id))
        return self._round_estimate

    def note_open(self, trade: dict, *, seq: int, atr_px: float | None = None,
                  now_wall: float = 0.0) -> None:
        """建 / 续开仓断面（同一 `seq` 的第二次调用 = 部分成交 ⇒ **幂等累加**，绝不重置估计）。"""
        if self._open is not None and self._open.seq == int(seq):
            self._open = merge_open_leg(self._open, trade, self.ct_val)
            return
        self._open = open_snapshot(trade, seq=seq, now_wall=now_wall, atr_px=atr_px,
                                   ct_val=self.ct_val, estimate=self._take_estimate(trade))

    def settle_close(self, trade: dict, *, exit_source: str = "unknown") -> dict:
        """结算本笔往返并返回 `explain.meta` 的加法键；无开仓断面 / 结算失败 ⇒ `{}`（绝不抛）。

        与 `record_close` 分开的理由是**顺序**：`exitSource` 要到 `_inject_explain_meta` 的最后一步
        才写（[P1-1]），而 `record_close` 发生在 `_dispatch` 更早的位置。故结算延后到打标之后。
        """
        snap, self._open = self._open, None
        if snap is None:
            return {}
        trip = settle(snap, trade, SettleContext(net_mode=self.net_mode, ct_val=self.ct_val,
                                                 exit_source=exit_source))
        if trip is None:
            return {}
        self._settled.append(trip)
        return trip_meta_wire(trip)

    def settled_trips(self) -> list[SettledTrip]:
        """会话级只读快照（轴 D 的会话段）。"""
        return list(self._settled)

    def last_settled(self) -> SettledTrip | None:
        """最近一笔已结算往返的**只读**访问器（experience-context-graph §4.3，评审 P0-3）。

        经验层需要 `SettledTrip` **本体**而不是 `trip_meta_wire`：后者只发 17 个键，不含
        `fee_known` / `side` / `entry_ts` / `edge_error_bps`，而那四个正是口径闸、事件表的列与
        「估计偏差型」模板的输入。纯读、无副作用、绝不改 `_settled`。
        """
        return self._settled[-1] if self._settled else None

    def realized_stats(self) -> dict | None:
        """`aiStats.realized` 加法块；一笔已结算往返都没有 ⇒ `None`（整键缺席，不造空表）。"""
        trips = list(self._settled)
        if not trips:
            return None
        total = len(trips)
        r_values = [t.r_multiple for t in trips if t.r_multiple is not None]
        return {
            "trips": total,
            "medianMoveBps": _median([t.move_bps for t in trips]),
            "medianRealizedNetBps": _median([t.realized_net_bps for t in trips]),
            "medianRMultiple": _median(r_values),
            # R 的分母**不是** `trips`：无止损 / 合约乘数未知的往返算不出 R。两个 R 读数与它同行
            # 下发，否则卡片上那个中位数就是一个没有分母的比例（§一 的产品级不变式）。
            "rSamples": len(r_values),
            "reachedOneRShare": (round(sum(1 for v in r_values if abs(v) >= 1.0) / len(r_values),
                                       _ROUND_RATIO) if r_values else None),
            "medianStopAtrMult": _median([t.stop_atr_mult for t in trips]),
            "edgeErrorMedianBps": _median([t.edge_error_bps for t in trips]),
            "costErrorMedianBps": _median([t.cost_error_bps for t in trips]),
            # 两个分母就是「所有新读数都必须带 coverage」这条产品级不变式的落地。
            "estimateCoverage": round(sum(1 for t in trips if t.est_edge_bps is not None)
                                      / total, _ROUND_RATIO),
            "feeCoverage": round(sum(1 for t in trips if t.fee_known) / total, _ROUND_RATIO),
        }

    def record_close(self, trade: dict, *, net_mode: bool | None = None) -> None:
        """记一笔平仓成交（`session._dispatch` 是全部 executor 事件的唯一收口，故在那里挂点）。

        `cost_bps` / `edge_bps` 取本账本记住的最近一次门禁读数（`note_gate`），使
        `session.py` 不必自己维护这两个字段——那是业务状态，不该住在已超 800 行红线的会话里。
        `net_mode` 缺省即取构造期读数；显式传值仅供单测直接对拍两种口径。
        """
        mode = self.net_mode if net_mode is None else bool(net_mode)
        pnl = _finite(trade.get("pnl"))
        fee = _finite(trade.get("fee"))
        gross = pnl + fee if mode else pnl
        net = pnl if mode else pnl - fee
        self._close_samples += 1
        if fee != 0.0:
            self._close_with_fee += 1
        self._lifetime["trips"] += 1
        self._lifetime["gross"] += gross
        self._lifetime["fee"] += fee
        self._lifetime["net"] += net
        self._lifetime["absGross"] += abs(gross)
        self._trips.append(RoundTrip(
            ts=int(trade.get("ts") or 0), side=str(trade.get("side") or ""),
            ord_type=str(trade.get("ordType") or "market"),
            gross_pnl_usd=gross, fee_usd=fee, net_pnl_usd=net,
            notional_usd=(_finite(trade.get("price")) * _finite(trade.get("qty"))
                          * self.ct_val),
            cost_bps=self._last_cost_bps, edge_bps=self._last_edge_bps))

    def last_close_net_usd(self) -> float | None:
        """最近一笔平仓成交的**净额**盈亏（`record_close` 那一份），一笔都没有 ⇒ `None`。

        存在的理由（execution-trend-verdict 评审 RP0-5 的实施落点）：顺势 / 逆势的成绩卡要在
        平仓结算时回流净额，而 spec 明写「口径**取自本模块 `record_close` 已算好的 `net`**，
        不另算」——两处各算一次必漂移，而那两个数会并排出现在同一张卡上。合入前本模块没有
        任何**公开**读点，调用方只能去摸 `_trips`；本访问器就是为堵掉那条路而加的。

        纯读、无副作用、绝不改 `_trips`。
        """
        return self._trips[-1].net_pnl_usd if self._trips else None

    def note_gate(self, status: str, *, cost_bps: float | None = None,
                  edge_bps: float | None = None) -> None:
        """记一轮门禁判定（pass / veto / unavailable）并留存本轮成本与 edge 读数。"""
        if status in self._gate:
            self._gate[status] += 1
        self._last_cost_bps = cost_bps
        self._last_edge_bps = edge_bps

    def note_round(self, executed: bool) -> None:
        """记一个决策轮及其是否产生了成交（决策转化率的分子 / 分母）。"""
        self._rounds += 1
        if executed:
            self._traded_rounds += 1

    def stats(self) -> dict:
        """归因读数（`§6.3` 的三组加法键；样本不足的量一律 `None`，绝不造假显著性）。"""
        trips = list(self._trips)
        gross = sum(t.gross_pnl_usd for t in trips)
        fee = sum(t.fee_usd for t in trips)
        net = sum(t.net_pnl_usd for t in trips)
        abs_gross = sum(abs(t.gross_pnl_usd) for t in trips)
        gates = sum(self._gate.values())
        return {
            "cost": {
                # RV-13：分母是**平仓**成交样本数（开仓腿也记费，但 G1 的阻断判据说的是
                # 「每笔平仓成交都带非零费率」，键名必须自证是哪一个分母）。
                "closeFeeCoverage": (round(self._close_with_fee / self._close_samples,
                                           _ROUND_RATIO) if self._close_samples else None),
                "roundTrips": len(trips),
                "grossPnlUsd": round(gross, _ROUND_USD),
                "feeUsd": round(fee, _ROUND_USD),
                "netPnlUsd": round(net, _ROUND_USD),
                "netExpectancyUsd": round(net / len(trips), _ROUND_USD) if trips else None,
                "costRatio": round(fee / abs_gross, _ROUND_RATIO) if abs_gross > 0 else None,
                "tStat": _t_stat([t.net_pnl_usd for t in trips]),
                "modelCostBps": self._last_cost_bps,
                # [P1-1] 全程累计口径子块（加法键；≤200 笔时与上面四项逐位相等，A-6 对拍）。
                # 周期验收卡的第 4、5 行**一律取这里**，绝不取上面那个滚动窗口口径。
                "lifetime": self.lifetime_cost(),
            },
            "edgeGate": {**self._gate,
                         "vetoRate": (round(self._gate["veto"] / gates, _ROUND_RATIO)
                                      if gates else None)},
            "decisionConversionRate": (round(self._traded_rounds / self._rounds, _ROUND_RATIO)
                                       if self._rounds else None),
        }

    def lifetime_cost(self) -> dict:
        """全程累计的成本四项 + 比例（[P1-1]；周期验收卡的唯一成本入口）。

        与 `stats()["cost"]` 的同名键**口径不同**：后者是最近 200 笔往返的滚动窗口。两者在
        ≤200 笔时逐位相等，超过后必然分叉——故两处文案都必须带口径词（R-13 / W-14）。
        """
        life = self._lifetime
        abs_gross = life["absGross"]
        return {"roundTrips": int(life["trips"]),
                "grossPnlUsd": round(life["gross"], _ROUND_USD),
                "feeUsd": round(life["fee"], _ROUND_USD),
                "netPnlUsd": round(life["net"], _ROUND_USD),
                "absGrossPnlUsd": round(abs_gross, _ROUND_USD),
                "costRatio": (round(life["fee"] / abs_gross, _ROUND_RATIO)
                              if abs_gross > 0 else None)}


def _median(values: list) -> float | None:
    """可得样本的中位数；一个都算不出 ⇒ `None`（`statistics.median` 对空列表会抛，端点上就是 500）。"""
    rows = sorted(float(v) for v in values if v is not None)
    if not rows:
        return None
    mid = len(rows) // 2
    out = rows[mid] if len(rows) % 2 else (rows[mid - 1] + rows[mid]) / 2
    return round(out, _ROUND_RATIO)


def _t_stat(values: list[float]) -> float | None:
    """单样本 t 统计量（`mean / (std_ddof1 / √n)`）；`n < 2` 或零方差 ⇒ `None`。"""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var <= 0:
        return None
    return round(mean / (math.sqrt(var) / math.sqrt(n)), _ROUND_RATIO)


def _finite(value) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0
