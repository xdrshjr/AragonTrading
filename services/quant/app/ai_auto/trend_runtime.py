"""执行周期趋势基调的**会话门面**（ai-trade-execution-trend-verdict 轴 E）。

镜像 `discipline_runtime` / `prior_runtime` / `horizon_runtime` 的既有形态：判定全在纯核心
（`trend_verdict.py`），本模块只持会话级计数与一张跨轮认领表——`session.py` 已 3390 行，
不得再往里塞任何业务状态。

## 本模块最重要的一条：alignment 必须在**开仓委托发出的那一刻定格**（评审 RP0-5）

「顺势 / 逆势各自成交了几笔、各自赚了多少」这件事**没有**跨轮载体是不行的：

- 开仓腿**可能跨轮到达**（限价挂单；`attribution._take_estimate` 就是为这件事存在的）；
- 已实现**净额盈亏**要到**平仓结算**（`attribution.settle_close`）才出现，那时距开仓可能隔了
  几十轮、基调早已翻面。

读「当轮基调」去归因，会把成绩系统性地记到错误的一侧，而**单轮用例恒绿**。故本模块的认领链是：

`note_alignment(alignment)`（下单**之前**）→ `claim_alignment(resting)` 挂进 `_pending`（形状与 `AttributionLedger._pending_estimates`
逐字同款，含同款的容量上限与轮末兜底清理）→ `note_fill` 按 `clOrdId` 认领（市价单回退到本轮
槽位，同 `_take_estimate` 的两级姿态）→ 认领到的那一个**冻结成本持仓周期的归属**
（`_open_alignment`，镜像 `AttributionLedger._open`）→ `note_settled` 在平仓结算时消费它。

认领不到 ⇒ 计入 `unknown`，**绝不猜**（接管仓位 / 离线平仓 / 重启后续跑都会命中这一格）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .open_funnel import wants_open
from .trend_verdict import (
    BONUS_LANE_APPLIED,
    BONUS_LANE_CONFLICT,
    BONUS_LANE_NO_NATIVE_VOTE,
    BONUS_LANE_NOT_ALIGNED,
    REASON_COUNTER_CONFIDENCE,
    TrendVerdictRule,
    alignment_of,
)
from .trip_ledger import PENDING_ESTIMATE_MAX

# 基调分布的四格（`unknown` = 本轮算不出基调，与「中性」是两个不同的结论）。
DIRECTIONS = ("up", "down", "range", "unknown")
# 顺势 / 逆势 / 归属不明的三格。**与 `open_funnel.SIDES` 同姿态**：第三格叫 `unknown` 而不是
# `neutral`（评审 RP2-5），且它**单列、绝不并进任何一侧**。
BUCKETS = ("aligned", "counter", "unknown")
UNKNOWN_BUCKET = "unknown"

# 顺势放宽通路的五个计数键（`lane` → wire 键）。`blockedNotAligned` 回答「有多少轮是逆势轮」，
# 它与另外两个 blocked 一起构成 RK-1 缓解④ 的读点：若长期只有一侧被放宽，用户在同一张卡上
# 就能看出来并关掉对应总闸。
_LANE_KEYS = {
    BONUS_LANE_APPLIED: "voteApplied",
    BONUS_LANE_CONFLICT: "blockedConflict",
    BONUS_LANE_NO_NATIVE_VOTE: "blockedNoNativeVote",
    BONUS_LANE_NOT_ALIGNED: "blockedNotAligned",
}
_LANE_WIRE_KEYS = ("voteApplied", "allowanceApplied", "blockedNoNativeVote",
                   "blockedConflict", "blockedNotAligned")

_ROUND_USD = 4


@dataclass
class TrendCounters:
    """会话级累计计数（**不进任何 deque**，口径 = 本会话全程累计）。

    Attributes:
        verdict_rounds: 基调方向 → 判了几轮（`unknown` = 算不出来的那些轮）。
        intents_by_alignment: 顺势 / 逆势 / 方向不可辨的**开仓意愿**数（判据逐字复用
            `open_funnel.wants_open`，评审 RP1-1）。
        blocked_counter: 逆势闸各拦了几次（`confidence` / `size` 两个细分码）。
        passed_counter: 逆势**放行**的轮数。**它不是降级、不进 `blocked_counter`**
            （镜像 `open_funnel` 模块 docstring 第 2 条与 `resolve_consensus_gate` 不变式 5）。
        bonus_lanes: 两条顺势放宽通路各点亮 / 各被哪条护栏挡下几次。
        filled_by_alignment: 开仓腿成交（alignment 取自**认领表**，不是当轮基调）。
        pnl_by_alignment: 已实现净额盈亏（USDT，平仓结算时回流）。
    """

    verdict_rounds: dict = field(default_factory=dict)
    intents_by_alignment: dict = field(default_factory=dict)
    blocked_counter: dict = field(default_factory=dict)
    passed_counter: int = 0
    bonus_lanes: dict = field(default_factory=dict)
    filled_by_alignment: dict = field(default_factory=dict)
    pnl_by_alignment: dict = field(default_factory=dict)


def _bump(bucket: dict, key: str, delta: int = 1) -> None:
    bucket[key] = int(bucket.get(key, 0)) + delta


class TrendRuntime:
    """一个会话的趋势基调运行时（纯同步；无 IO、无 await；未装配 ⇒ 全部方法空转）。"""

    def __init__(self, rule: TrendVerdictRule | None = None, *, min_samples: int = 8) -> None:
        self.rule = rule
        self.min_samples = max(1, int(min_samples))
        self.counters = TrendCounters()
        self.verdict: dict | None = None          # 本轮基调（只读出口，供门禁与统计）
        self.bar: str | None = None
        self._armed = False
        # ---- alignment 的跨轮认领链（评审 RP0-5）----
        self._pending: dict[str, str] = {}        # clOrdId → 定格的 alignment
        self._round_alignment: str | None = None  # 轮级槽（市价单的回退，轮末清零）
        self._open_alignment: str | None = None   # 当前持仓周期的归属（结算时消费）

    # ---------- 每轮的三个记点 ----------
    def note_verdict(self, verdict: dict | None) -> None:
        """本轮基调（`_build_decision_payload` 装配完即调）；`None` ⇒ 不计数、只清当轮出口。"""
        self.verdict = verdict
        if not verdict:
            return
        self._armed = True
        self.bar = verdict.get("bar") or self.bar
        direction = str(verdict.get("direction") or "unknown")
        _bump(self.counters.verdict_rounds,
              direction if direction in DIRECTIONS else "unknown")

    def note_intent(self, action: str, degrade: str | None, verdict: dict | None) -> None:
        """开仓意愿的分母（落点与 `OpenFunnelCounters.note_intent` 完全一致：`decide` 返回后、
        **任何门禁之前**——门禁后的动作已被洗成 hold，在那里数分母永远是 0）。

        **判据逐字复用 `open_funnel.wants_open`**（评审 RP1-1）：`low_confidence` 轮此刻
        `action` 已被 `_map_decision` 洗成 `hold`，照「只看 action」实现会把**每一轮**（含纯观望
        轮）都计进来，卡面那行「开仓意愿」当场退化成轮数计数器。方向不可辨的开仓意愿计入
        `unknown` 一格，**绝不猜方向**。
        """
        if not self._armed or not wants_open(action, degrade):
            return
        bucket = alignment_of(action, verdict)
        _bump(self.counters.intents_by_alignment,
              UNKNOWN_BUCKET if bucket == "neutral" else bucket)

    def note_gate(self, outcome) -> None:
        """逆势闸的一次判定（`lane == "counter"` 是**放行**，绝不进 `blocked_counter`）。"""
        lane = getattr(outcome, "lane", None)
        if lane == "counter":
            self.counters.passed_counter += 1
            return
        if lane != "veto":
            return
        reason = str(getattr(outcome, "reason", "") or "")
        _bump(self.counters.blocked_counter,
              "confidence" if reason.startswith(REASON_COUNTER_CONFIDENCE) else "size")

    def note_bonus(self, lane: str, opposing_allowance: int) -> None:
        """两条顺势放宽通路的归因计数（RK-1 缓解④ 的读点）。

        `lane` 为 `"none"`（总闸关 / 基调不可用）时什么都不记——那不是「被护栏挡下」，把它记进
        blocked 会让「本轮压根没有基调」与「基调有但不顺势」混成同一个数。
        """
        key = _LANE_KEYS.get(str(lane))
        if key:
            _bump(self.counters.bonus_lanes, key)
        if int(opposing_allowance or 0) > 0:
            _bump(self.counters.bonus_lanes, "allowanceApplied")

    # ---------- alignment 的跨轮认领链（评审 RP0-5）----------
    def note_alignment(self, alignment: str) -> None:
        """★ 第一步：把本轮的 alignment 置进**轮级槽**，落点必须排在 `_execute_decision`
        **之前**（实施期发现 F-7）。

        为什么不能只有一个 `note_submitted(clOrdId, alignment)`（spec §3.7.1 的原签名）：
        那个签名只能在 `_execute_decision` **返回之后**调用（挂单的 `clOrdId` 只在
        `executor.resting` 上，而它要到下单之后才存在），可**市价单的成交事件是在
        `_execute_decision` 内部经 `_dispatch` 发出的**——那一刻轮级槽还是空的，于是**每一笔
        市价开仓腿都会落进 `unknown`**，而卡面照样有数、不报任何错。

        `AttributionLedger` 早就为同一件事拆成了两个入口（`note_estimate` 在门禁那一步、
        `claim_estimate` 在下单之后），本模块逐字沿用那条已被验证的姿态。

        `alignment` 不在三格内（`neutral` = 非开仓 / 基调不可用）⇒ **不置位**：那一轮的成交
        本来就该落进 `unknown`，绝不猜。
        """
        if alignment in BUCKETS:
            self._round_alignment = alignment

    def claim_alignment(self, resting) -> None:
        """★ 第二步：下单那一刻把轮级 alignment 移进**按 `clOrdId` 的认领表**（跨轮载体）。

        入参是 `executor.resting`（`RestingOrder | None`）而不是一个 `clOrdId` 字符串：
        `_execute_decision` 的回执 dict 里**没有** `clOrdId` 这个键，挂单的委托号只在
        `executor.resting` 上（`CostRuntime.claim_estimate` 已为同一件事踩过这个坑）。
        属性按**鸭子类型**读，本层不 import 那个类型。

        非开仓挂单 / 无挂单（市价单）⇒ 空转：那种成交与本轮同步到达，轮级槽已经够用。
        限价开仓单则挂进认领表，使 alignment 能跨若干轮活到成交那一刻。容量上限
        `PENDING_ESTIMATE_MAX`，超量按 FIFO 丢弃（挂单额度只有一个，正常只会有 1 项）。
        """
        if getattr(resting, "intent", None) != "open" or self._round_alignment is None:
            return
        cl_ord_id = str(getattr(resting, "cl_ord_id", "") or "")
        if not cl_ord_id:
            return
        while len(self._pending) >= PENDING_ESTIMATE_MAX:
            self._pending.pop(next(iter(self._pending)))
        self._pending[cl_ord_id] = self._round_alignment

    def _claim(self, trade: dict) -> str:
        """开仓成交认领 alignment：**先按 `clOrdId`**（限价跨轮），再回退轮级槽；都取不到 ⇒
        `unknown`（接管仓位 / 离线平仓 / 重启后续跑都会命中这一格，**绝不猜**）。"""
        cl_ord_id = (trade or {}).get("clOrdId")
        if cl_ord_id and str(cl_ord_id) in self._pending:
            return self._pending.pop(str(cl_ord_id))
        return self._round_alignment or UNKNOWN_BUCKET

    def note_fill(self, trade: dict) -> None:
        """开仓腿成交（`_dispatch` 里 `side ∈ ("long","short")` 的 trade 事件）。

        认领到的归属同时**冻结成本持仓周期的归属**（`_open_alignment`，镜像
        `AttributionLedger._open`）——平仓结算可能隔几十轮才到，那时基调早已翻面。
        同一持仓周期的部分成交只在第一条腿上冻结，后续腿不覆盖。
        """
        if not self._armed:
            return
        bucket = self._claim(trade)
        _bump(self.counters.filled_by_alignment, bucket)
        if self._open_alignment is None:
            self._open_alignment = bucket

    def note_settled(self, net_pnl_usd: float | None) -> None:
        """平仓结算时回流**净额**盈亏，并释放本持仓周期的归属。

        口径**取自 `AttributionLedger` 已算好的 `net`**，本模块不另算——两处各算一次必漂移，
        而这两个数会并排出现在同一张卡上。
        """
        if not self._armed:
            return
        bucket = self._open_alignment or UNKNOWN_BUCKET
        self._open_alignment = None
        try:
            value = float(net_pnl_usd)
        except (TypeError, ValueError):
            return
        self.counters.pnl_by_alignment[bucket] = round(
            float(self.counters.pnl_by_alignment.get(bucket, 0.0)) + value, _ROUND_USD)

    def end_round(self) -> None:
        """轮末兜底清零轮级槽（镜像 `AttributionLedger.end_round` 的 D-3）：一次被否的开仓意图
        绝不可改绑到下一笔真实成交上。**按 `clOrdId` 的认领表不清**——它本来就要跨轮活着。"""
        self._round_alignment = None

    # ---------- 只读出口 ----------
    def stats_wire(self) -> dict | None:
        """`aiStats.trendVerdict` 与 `status.trendVerdict` 的**同一份** wire；

        未装配 / 零样本 ⇒ `None`（整键缺席，不造空表）。**两条通道必须同时挂**（评审 RP1-2）：
        实时页读的是成员级 status 快照（`AiTradeStatsLive` 的 `m.session?.trendVerdict`），
        历史详情页读的才是 `aiStats.trendVerdict`——只落一处会让另一页那张卡恒为空。
        """
        if not self._armed or not self.counters.verdict_rounds:
            return None
        counters = self.counters
        return {
            "armed": True, "bar": self.bar,
            "verdictRounds": {key: int(counters.verdict_rounds.get(key, 0))
                              for key in DIRECTIONS},
            "intents": {key: int(counters.intents_by_alignment.get(key, 0))
                        for key in BUCKETS},
            "counterBlocked": {key: int(counters.blocked_counter.get(key, 0))
                               for key in ("confidence", "size")},
            "counterPassed": int(counters.passed_counter),
            "bonusLanes": {key: int(counters.bonus_lanes.get(key, 0))
                           for key in _LANE_WIRE_KEYS},
            "filled": {key: int(counters.filled_by_alignment.get(key, 0)) for key in BUCKETS},
            "netPnlUsd": {key: round(float(counters.pnl_by_alignment.get(key, 0.0)), _ROUND_USD)
                          for key in BUCKETS},
            "minSamples": self.min_samples,
        }

    def merge_into(self, ai_stats: dict) -> dict:
        """把趋势基调统计并进落库 `aiStats`（镜像 `CostRuntime.merge_into` 的**调用点合并**范式）。

        `_compute_ai_stats` 是**模块级纯函数**、拿不到 `self`，故「在其内部合并」在物理上不可
        实现（`stats_ext` 模块 docstring 第 1 条已成文）。未装配 / 零样本 ⇒ 整键缺席。
        """
        from .stats_ext import build_trend_stats, merge_trend_stats   # 延迟导入，避免环

        return merge_trend_stats(ai_stats, build_trend_stats(self.stats_wire()))

    def gate_counts(self) -> int:
        """纪律自检第 14 行的 `firedCount`：逆势闸本会话累计拦下的次数（两个细分码合计）。"""
        return sum(int(value) for value in self.counters.blocked_counter.values())
