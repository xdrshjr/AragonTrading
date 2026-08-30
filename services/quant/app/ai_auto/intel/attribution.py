"""情报引用的会话级归因运行时（ai-trade-intel-closed-loop 轴 B）。

每会话一份的轻对象，构造于 `AiTradeSession.__init__`（与 `self._experience` 同处）。
**未装配（`intel` 总闸关 / `attribution_enabled=False`）⇒ 全部方法空转**，不建任何状态。

四条贯穿本模块的纪律，每一条都对应一个本仓库栽过的坑：

1. **双槽绑定**（评审 P0-1）。`ai_trade_limit_orders_enabled` 默认 True，限价开仓单会 resting
   若干轮、成交由盯市 tick 上另一条 `_dispatch` 发出——那时本轮待写行早已被 `persist_pending`
   冲掉。故 `note_open` 逐行镜像 `AttributionLedger._take_estimate`：先按 `clOrdId` 在**跨轮
   认领槽**里认领，认领不到再回退本轮待写行（市价恒走回退）。
2. **`seq` 只能取 `trip.seq`**（评审 P0-2）。`_dispatch` 的 close 分支第一件事就是
   `self._position_seq += 1`，等 `note_close` 跑到时那个计数器已经是**下一个** epoch。
   取错的症状是结算 UPDATE 恒命中零行、`settled_count` 与 `net_pnl_usd` 恒 0，而**不报任何错**。
3. **净额只有一个产出点**。本层消费 `CostRuntime.last_settled_trip()`，**绝不**自己按成交价再
   算一次——自己算的那一份必然与成本记账口径分叉，而分叉不会报错。
4. **归因不产生 degrade**（承诺二）。本模块的任何异常都收敛成「统计列没更新」+ 一个可见的
   `dropped` 计数，**绝不**收敛成一个改变 `TradeDecision` 的降级码。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ...db import repositories as repo
from ...db.session import get_sessionmaker
from .citation import classify_outcome, is_settleable, resolve_citations

logger = logging.getLogger(__name__)

# 跨轮认领槽的容量上限（逐字沿用 `ai_auto/attribution.PENDING_ESTIMATE_MAX` 的做法）。挂单额度
# 只有一个，正常只会有 1 项；上限存在只是为了让一条永不成交的挂单不会把内存占住。超量 FIFO。
PENDING_CITE_CLAIM_MAX = 8


@dataclass
class PendingCitation:
    """一条尚未落盘的引用行（纯内存）。字段名与 `append_intel_citations` 的 payload 键一一对应。"""

    item_id: int
    ref_token: str
    round_no: int
    cited_ts: int
    proposed_action: str
    role: str = "hold"
    final_action: str = ""
    traded: bool = False
    position_seq: int = 0
    cl_ord_id: str = ""

    def payload(self, inst_id: str, run_id: int) -> dict:
        return {"itemId": self.item_id, "instId": inst_id, "runId": run_id,
                "roundNo": self.round_no, "citedTs": self.cited_ts,
                "refToken": self.ref_token, "clOrdId": self.cl_ord_id, "role": self.role,
                "proposedAction": self.proposed_action, "finalAction": self.final_action,
                "traded": self.traded, "positionSeq": self.position_seq}


@dataclass
class _SettleRequest:
    """一次待落盘的结算（`note_close` 记下、`persist_pending` 执行）。"""

    position_seq: int
    net_pnl_usd: float | None
    outcome: str
    hold_s: int
    exit_source: str


@dataclass
class _EntryClaim:
    """一次待落盘的入场升级（限价跨轮成交，P0-1 的落库侧）。"""

    cl_ord_id: str
    position_seq: int


class IntelAttribution:
    """会话级情报引用归因。全部方法**同步、纯内存**，只有 `persist_pending` 是异步。"""

    def __init__(self, inst_id: str, run_id: int, *, enabled: bool = False,
                 sessionmaker=None, now=time.time) -> None:
        """装配。

        Args:
            inst_id / run_id: 引用行的作用域（`run_id` 是 `int`，评审 P2-1）。
            enabled: 轴 B 总闸（`intel` 总闸 **且** `attribution_enabled`）。`False` ⇒ 全部方法
                空转、不建任何状态、一个字节都不写（G-5）。
            sessionmaker: 造 DB 会话的工厂（**函数本身**）；缺省取 `get_sessionmaker`。
            now: 墙钟取数（可注入，便于离线用例）。
        """
        self.inst_id = inst_id
        self.run_id = int(run_id or 0)
        self.enabled = bool(enabled)
        self._sessionmaker = sessionmaker
        self._now = now
        # 轮级待写行：`note_decision` 建、`persist_pending` 落盘、`end_round` 丢弃。
        self._round_rows: list[PendingCitation] = []
        # 跨轮认领槽（P0-1）：`clOrdId → 已落盘的引用行数`。**`end_round` 不清它**——那正是限价单
        # 要跨轮活下来的东西。
        self._claims: dict[str, int] = {}
        self._pending_settles: list[_SettleRequest] = []
        self._pending_entries: list[_EntryClaim] = []
        self._stats = {"cited": 0, "fabricated": 0, "refWithoutDigest": 0,
                       "settledTrips": 0, "netPnlUsd": 0.0, "dropped": 0}

    # ---------- 决策轮 ----------
    def note_decision(self, decision, *, round_no: int, ts: int, refmap: dict | None) -> None:
        """裁决本轮 `intelRefs` 并生成轮级待写行（纯内存）。

        Args:
            decision: **`stage1_decision`**（E-4：任何门禁之前的那一个）。取门禁后的会让
                「模型采信了情报却被冷却门禁拦下」变成「模型没采信情报」。
            round_no / ts: 本轮编号与时刻。
            refmap: 本轮定稿的编号表（`digest["refs"]`）；缺席 ⇒ 全部 token 记 `refWithoutDigest`。
        """
        if not self.enabled:
            return
        refs = tuple(getattr(decision, "intel_refs", ()) or ())
        if not refs:
            return
        resolution = resolve_citations(refs, refmap)
        self._stats["fabricated"] += len(resolution.unknown)
        self._stats["refWithoutDigest"] += len(resolution.no_refmap)
        action = str(getattr(decision, "action", "") or "")
        for token, item_id in resolution.valid:
            self._round_rows.append(PendingCitation(
                item_id=int(item_id), ref_token=token, round_no=int(round_no),
                cited_ts=int(ts), proposed_action=action))
        self._stats["cited"] += len(resolution.valid)

    def note_executed(self, final_action: str, executed: dict, *, resting=None) -> None:
        """补齐本轮待写行的最终动作，并按需把它们冻结进跨轮认领槽（P0-1）。

        `resting` 的入参是 `executor.resting`（`RestingOrder | None`）而不是一个字符串：
        `_execute_decision` 的回执 dict 里**没有** `clOrdId` 这个键，挂单的委托号只在
        `executor.resting` 上（逐行镜像 `CostRuntime.claim_estimate`，同一处调用点、同一个
        `intent == "open"` 判据）。属性按鸭子类型读，本层不 import 那个类型。
        """
        if not self.enabled or not self._round_rows:
            return
        traded = bool((executed or {}).get("traded"))
        for row in self._round_rows:
            row.final_action = str(final_action or "")[:16]
            row.traded = traded
        if getattr(resting, "intent", None) != "open":
            return
        cl_ord_id = str(getattr(resting, "cl_ord_id", "") or "")
        if not cl_ord_id:
            return
        # 只冻结**尚未成交**的行：限价单在同一轮内即时成交时 `_dispatch` 已经把它们标成 `entry`
        # 并走了回退路径，再放一份进认领槽只会留下一条永远认领不到的记录（收尾时计成 `dropped`，
        # 让一个正常路径在面板上显示成一次丢弃）。
        claimable = [row for row in self._round_rows if row.role == "hold"]
        if not claimable:
            return
        for row in claimable:
            row.cl_ord_id = cl_ord_id
        while len(self._claims) >= PENDING_CITE_CLAIM_MAX:
            dropped_key = next(iter(self._claims))
            self._stats["dropped"] += self._claims.pop(dropped_key)
        self._claims[cl_ord_id] = self._claims.get(cl_ord_id, 0) + len(claimable)

    def end_round(self) -> None:
        """丢弃**尚未落盘**的轮级待写行并计数（评审 P1-9）。

        一次在 `note_decision` 之后、`persist_pending` 之前抛错的轮，会把它的引用行留到下一轮、
        记成下一轮的 `round_no` 与动作。先例逐字可抄：`AttributionLedger.end_round()`
        （「一次被否的开仓意图绝不可改绑到下一笔真实往返上」）。

        **跨轮认领槽不清**——那正是限价单要跨轮活下来的东西。
        """
        if self._round_rows:
            self._stats["dropped"] += len(self._round_rows)
            self._round_rows = []

    # ---------- 成交事件 ----------
    def note_open(self, trade: dict, *, seq: int) -> None:
        """开仓成交 ⇒ 把对应的引用行标成 `entry` 并绑定持仓周期。

        取行顺序**逐字镜像 `AttributionLedger._take_estimate`**：先按 `trade["clOrdId"]` 在跨轮
        认领槽里认领（限价跨轮），认领不到再回退本轮待写行（市价恒走回退）；两条路都取不到就
        什么都不做（**不猜、不改绑**）。
        """
        if not self.enabled:
            return
        cl_ord_id = str((trade or {}).get("clOrdId") or "")
        if cl_ord_id and cl_ord_id in self._claims:
            self._claims.pop(cl_ord_id, None)
            self._pending_entries.append(_EntryClaim(cl_ord_id, int(seq)))
            return
        for row in self._round_rows:
            row.role = "entry"
            row.position_seq = int(seq)

    def note_close(self, trade: dict, meta: dict, trip) -> None:
        """平仓成交 ⇒ 标记本轮待写行为 `exit`，并把该 epoch 的全部行排进结算队列。

        **必须排在 `_inject_explain_meta` 之后**（E-5）：`exitSource` 与往返结算都在那一步才写进
        meta，挂早了 meta 是空的。

        Args:
            trade: 平仓成交。
            meta: `trade["explain"]["meta"]`；本层只读 `exitSource`。
            trip: `SettledTrip`，由调用方从 `CostRuntime.last_settled_trip()` 取（E-6）。
                ``None`` ⇒ 接管仓 / 离线平仓，`CostRuntime` 没有它的开仓断面 ⇒ 该 epoch 写
                `settled=True, outcome="void"`：**结算尝试确实发生过、净额确实不可得**。
                不写 `settled=True` 的话那些行会永远挂在待结算集合里，而它们的 epoch 已经不会
                再回来了——一个永远不收敛的待结算集合比一个诚实的 `void` 更难解释。
        """
        if not self.enabled:
            return
        for row in self._round_rows:
            row.role = "exit"
        # ★ P0-2：`seq` 只能取 `trip.seq`。`self._position_seq` 在本方法跑到时已经 +1 了。
        seq = int(getattr(trip, "seq", 0) or 0) if trip is not None else 0
        net = None if trip is None else getattr(trip, "net_pnl_usd", None)
        outcome = classify_outcome(net)
        for row in self._round_rows:
            row.position_seq = seq
        self._pending_settles.append(_SettleRequest(
            position_seq=seq, net_pnl_usd=(None if outcome == "void" else float(net or 0.0)),
            outcome=outcome,
            hold_s=int(float(getattr(trip, "hold_seconds", 0.0) or 0.0)) if trip else 0,
            exit_source=str((meta or {}).get("exitSource") or "")))
        if outcome != "void":
            self._stats["settledTrips"] += 1
            self._stats["netPnlUsd"] = round(
                float(self._stats["netPnlUsd"]) + float(net or 0.0), 6)

    # ---------- 落盘 ----------
    async def persist_pending(self) -> None:
        """一次提交：INSERT 新引用行 → 升级入场行 → 结算 → 累加条目统计列。

        **best-effort**：任何失败只 warning 并**丢弃**本批（不重试、不堆积）。理由与
        `_persist_trade` 同源：归因是富化，成交与决策是账实相符；一次富化异常绝不允许换来一次
        没推出去的决策事件。**但**丢弃条数必须累加到 `stats()["dropped"]` 并经 `aiStats` 下发
        ——静默丢弃正是本仓库反复栽的那个坑。
        """
        if not self.enabled:
            return
        rows = [row for row in self._round_rows]
        self._round_rows = []
        entries = list(self._pending_entries)
        settles = list(self._pending_settles)
        self._pending_entries = []
        self._pending_settles = []
        if not rows and not entries and not settles:
            return
        sessionmaker = self._sessionmaker or get_sessionmaker
        try:
            async with sessionmaker()() as session:
                if rows:
                    await repo.append_intel_citations(
                        session, [row.payload(self.inst_id, self.run_id) for row in rows],
                        commit=False)
                for entry in entries:
                    await repo.promote_intel_citations_to_entry(
                        session, run_id=self.run_id, cl_ord_id=entry.cl_ord_id,
                        position_seq=entry.position_seq, commit=False)
                now = int(self._now())
                for settle in settles:
                    await repo.settle_intel_citations(
                        session, run_id=self.run_id, position_seq=settle.position_seq,
                        net_pnl_usd=settle.net_pnl_usd, hold_s=settle.hold_s,
                        exit_source=settle.exit_source, outcome=settle.outcome, now=now,
                        commit=False)
                await session.commit()
        except Exception as exc:  # noqa: BLE001 —— 见 docstring（归因绝不阻断成交与决策）
            self._stats["dropped"] += len(rows)
            logger.warning("ai-trade intel attribution persist failed (%s): %s",
                           self.inst_id, exc)

    def stop(self) -> None:
        """关停：尚未落盘的待写行与认领槽**一并丢弃并计入 `dropped`**（评审 P2-12）。

        **不新增任何关停步骤**——`_shutdown_steps` 的第 7 步（`ai_trade_intel`）已被
        `test_ai_intel_wiring.py` 冻结，为了几条归因明细去动那张表，风险远大于收益。
        """
        self._stats["dropped"] += len(self._round_rows) + sum(self._claims.values())
        self._round_rows = []
        self._claims = {}

    def stats(self) -> dict | None:
        """会话内累计（`snapshot()` / `aiStats.intel` 共用；未装配 ⇒ `None`）。"""
        if not self.enabled:
            return None
        return dict(self._stats)


def outcome_of(net_pnl_usd: float | None) -> str:
    """`classify_outcome` 的转发（供 session 侧在不 import 纯核心的情况下取同一口径）。"""
    return classify_outcome(net_pnl_usd)


__all__ = ["IntelAttribution", "PendingCitation", "PENDING_CITE_CLAIM_MAX", "is_settleable",
           "outcome_of"]
