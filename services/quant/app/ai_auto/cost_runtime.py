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
from .cost_model import estimate_round_trip_cost, query_for_decision, resolve_cost_params
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
        # 信号先验的本轮只读视图（`PriorRuntime.coverage_wire()` 每轮回灌一次）。**两条快照通道
        # 互斥且不同源**：`snapshot()` 只在门禁生效（k_min>0）时随 `edge_gate` 事件下发，
        # `snapshot_if_due()` 只在门禁空转时搭 `status` 事件——只并入其中一条，运维按 runbook 去
        # 另一条里找 `priorCoverage` 会永远找不到（P1-3）。
        self._prior_view: dict | None = None

    # ---------- 盯市 tick（唯一的网络 IO 落点；决策轮内零 IO）----------
    async def refresh_funding(self, now_wall: float) -> bool:
        """5s 盯市上刷新资金费率快照；未到 TTL / 已禁用 / 取数失败一律静默 `False`。"""
        return await self.funding.refresh(get_rest_client().get_funding_rate, now_wall)

    def set_prior_view(self, view: dict | None) -> None:
        """回灌本轮先验读数（`None` ⇒ 全部先验键缺席）；先验读数进成本快照的**唯一入口**。"""
        self._prior_view = view or None

    # ---------- 决策轮（纯同步）----------
    def evaluate(self, decision, *, last_px: float | None, atr_px: float | None,
                 take_profit_pct: float | None, bar_seconds: int,
                 prior_ev_bps: float | None = None,
                 prior_binding: bool = False) -> EdgeGateOutcome | None:
        """本轮门禁判定；`k_min <= 0` ⇒ `None`（门禁空转，调用方据此不发任何事件）。

        `prior_ev_bps` 无条件透传（读数 + 反事实进 wire），`prior_binding`（总闸③）才决定它是否
        真的进 `min` 链。`priorP` / `priorContributors` 由本层从 `_prior_view` 补进 wire——纯核心
        `edge_gate` 只认得 bps，不该为了展示字段而认识先验的数据结构。
        """
        out = evaluate_edge_gate(
            decision, settings=self.settings, last_px=last_px, atr_px=atr_px,
            take_profit_pct=take_profit_pct, bar_seconds=bar_seconds,
            funding=self.funding.snapshot(time.time()),
            prior_ev_bps=prior_ev_bps, prior_binding=prior_binding,
        )
        if out is not None:
            side = {"open_long": "long", "open_short": "short"}.get(decision.action)
            readout = ((self._prior_view or {}).get(side) or {}) if side else {}
            out.wire["priorP"] = readout.get("p")
            out.wire["priorContributors"] = readout.get("contributors")
        return out

    def estimate_cost_only(self, decision, bar_seconds: int) -> float | None:
        """只读往返成本估算（bps），**不受 `k_min` 门控**（vol-sizing §4.2 / Q-2）。

        `evaluate` 在 `k_min <= 0`（默认值）时整体早退返回 `None`，故仓位层的凯利项若走那条路会**默认
        恒不可算**——那等于「为了让一个仓位特性生效，运维必须先打开另一层会真实否决开仓的资金门禁」。
        本入口复用**同一对**纯核心（`estimate_round_trip_cost` + `resolve_cost_params`，`evaluate_edge_gate`
        用的也是这两行），因此**不是第二条估算路径、不存在口径漂移**：`k_min` 早退只发生在编排函数里，
        两个估算纯核心都是模块级、无门控。

        资金费率快照在 `k_min=0` 时不刷新（构造期的 `enabled` 条件），故此路径拿到的成本
        `confidence="degraded"`——**如实透传**，不做任何补齐；`degraded` 只会让成本偏保守，不影响可算性。

        Returns:
            往返成本（bps）；非开仓动作照样可算（纯函数不看动作），调用方自行决定用不用。
        """
        cost = estimate_round_trip_cost(
            query_for_decision(decision, bar_seconds=bar_seconds,
                               funding=self.funding.snapshot(time.time()),
                               funding_enabled=self.settings.ai_trade_funding_enabled),
            resolve_cost_params(self.settings))
        return cost.total_bps

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
        return self._with_prior(build_cost_stats(self.attribution.stats(), self.fee_ledger))

    def snapshot_if_due(self, round_no: int) -> dict | None:
        """L0/L1 通道的节流快照（§5.4）；未到轮次 ⇒ `None`。"""
        snap = cost_snapshot_wire(round_no, self.attribution.stats(), self.fee_ledger)
        return None if snap is None else self._with_prior(snap)

    def _with_prior(self, stats: dict) -> dict:
        """并入先验覆盖率读数（两条通道同源，P1-3）；未回灌视图 ⇒ 两键**缺席**而非 None。

        `priorCoverage` 是**轮级瞬时量**：落 `metricsJson.aiStats` 的 `merge_into` 走的也是
        `snapshot()`，故会话停止后记的是**最后一轮**的读数，绝不可被误读成全程平均。
        """
        view = self._prior_view
        if view is None:
            return stats
        stats["priorCoverage"] = view.get("coverage")
        stats["priorAgeS"] = view.get("ageS")
        return stats

    def merge_into(self, ai_stats: dict) -> dict:
        """把三组加法键并入既有 `aiStats`（键冲突即抛，绝不静默覆盖，§3.8）。"""
        return merge_cost_stats(ai_stats, self.snapshot())
