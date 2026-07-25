"""会话侧成本运行时（ai-trade-cost-accounting-and-edge-gate §3.10）。

把「资金费率快照 / 往返归因 / 门禁判定 / 统计装配」四件事的**状态与编排**从 `AiTradeSession` 里搬
出来，使会话只剩薄封装调用。这不是新造框架，而是逐字复用本仓库已有的同款范式——
`profit_guard_runtime.ProfitGuardRuntime` 正是为同一个理由（`session.py` 早已超 `CLAUDE.md` 的 800
行红线，只允许新增薄封装调用）存在的，本模块与它同构：纯同步核心 + 会话只留 async 编排点。

`fee_ledger is None`（理论上不会发生于 AI 会话，但直接构造会话的既有单测会出现）时全部实测键缺席，
其余读数照常——归因只依赖 `trade` 字段，不依赖账本。
"""
from __future__ import annotations

import time

from ..live_auto.fee_ledger import explain_meta_fields
from ..okx.rest_client import get_rest_client
from .attribution import AttributionLedger
from .edge_gate import EdgeGateOutcome, evaluate_edge_gate
from .funding import FundingCache
from .stats_ext import build_cost_stats, cost_snapshot_wire, merge_cost_stats


class CostRuntime:
    """一个会话的成本层运行时：资金费率缓存 + 归因账本 + 门禁编排 + 统计装配。"""

    def __init__(self, inst_id: str, settings, fee_ledger) -> None:
        self.settings = settings
        self.fee_ledger = fee_ledger
        # 门禁空转（默认 `k_min=0`）时资金费率**一个请求都不发**：那个数此时没有任何消费方，
        # 白烧公共配额还给多标的分组平添扇出（见 spec §十二 I-2）。
        self.funding = FundingCache(
            inst_id, ttl_s=settings.ai_trade_funding_poll_ttl_s,
            enabled=bool(settings.ai_trade_funding_enabled)
            and float(settings.ai_trade_edge_gate_k_min) > 0)
        # A0-2 口径是**会话级静态**（开关在启动时读一次）：归因账本据此把 trade["pnl"] 正确
        # 还原为毛 / 净两侧——搞反会让归因表在切换口径的那一刻整体偏移一个往返成本。
        self.attribution = AttributionLedger(
            net_mode=bool(settings.ai_trade_net_pnl_enabled),
            ct_val=getattr(fee_ledger, "ct_val", 0.0))
        self.unavailable_streak = 0        # 连续「估算不可用」轮数（§3.7 告警升级）

    # ---------- 盯市 tick（唯一的网络 IO 落点；决策轮内零 IO）----------
    async def refresh_funding(self, now_wall: float) -> bool:
        """5s 盯市上刷新资金费率快照；未到 TTL / 已禁用 / 取数失败一律静默 `False`。"""
        return await self.funding.refresh(get_rest_client().get_funding_rate, now_wall)

    # ---------- 决策轮（纯同步）----------
    def evaluate(self, decision, *, last_px: float | None, atr_px: float | None,
                 take_profit_pct: float | None, bar_seconds: int) -> EdgeGateOutcome | None:
        """本轮门禁判定；`k_min <= 0` ⇒ `None`（门禁空转，调用方据此不发任何事件）。"""
        return evaluate_edge_gate(
            decision, settings=self.settings, last_px=last_px, atr_px=atr_px,
            take_profit_pct=take_profit_pct, bar_seconds=bar_seconds,
            funding=self.funding.snapshot(time.time()),
        )

    def note_gate(self, out: EdgeGateOutcome) -> int:
        """记一轮门禁判定 + 推进连续不可用计数；返回当前连续轮数（0 = 本轮可用 / 本轮不受约束）。

        **只有受约束的轮次进计数**（`out.gated`）：观望 / 平仓 / 撤单轮门禁无事可做，把它们记成
        「放行」会稀释 `vetoRate`（§7.5 的 L2 放行判据），记成「不可用」则会误触发不可用告警。
        """
        if not out.gated:
            return 0
        self.attribution.note_gate(out.status, cost_bps=out.cost_bps, edge_bps=out.edge_bps)
        self.unavailable_streak = (self.unavailable_streak + 1
                                   if out.status == "unavailable" else 0)
        return self.unavailable_streak

    def note_round(self, executed: bool) -> None:
        """记一个决策轮及其是否落到成交（决策转化率，§6.3）。"""
        self.attribution.note_round(executed)

    def record_close(self, trade: dict) -> None:
        """记一笔平仓成交（`session._dispatch` 是全部 executor 事件的唯一收口）。"""
        self.attribution.record_close(trade)

    def apply_explain_meta(self, meta: dict, trade: dict) -> None:
        """把成交的费率 / 滑点键搬进 `explain.meta`；A0-1 关闭 ⇒ 空转 ⇒ meta 逐字回退。"""
        for key, value in explain_meta_fields(trade).items():
            meta.setdefault(key, value)

    # ---------- 统计（会话内快照与落库富化出自同一 build_cost_stats）----------
    def snapshot(self) -> dict:
        return build_cost_stats(self.attribution.stats(), self.fee_ledger)

    def snapshot_if_due(self, round_no: int) -> dict | None:
        """L0/L1 通道的节流快照（§5.4）；未到轮次 ⇒ `None`。"""
        return cost_snapshot_wire(round_no, self.attribution.stats(), self.fee_ledger)

    def merge_into(self, ai_stats: dict) -> dict:
        """把三组加法键并入既有 `aiStats`（键冲突即抛，绝不静默覆盖，§3.8）。"""
        return merge_cost_stats(ai_stats, self.snapshot())
