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

_CAPACITY = 200
_ROUND_USD = 6
_ROUND_RATIO = 4


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
