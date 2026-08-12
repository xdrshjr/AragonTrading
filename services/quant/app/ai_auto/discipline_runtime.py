"""会话侧的纪律自检门面（ai-trade-discipline-preset-and-integrity 轴 B / 轴 C）。

镜像 `CostRuntime` / `ProfitGuardRuntime` / `HorizonRuntime` 的既有下沉范式：状态、计数、装配全在
这里，`session.py` 只留三行薄调用（`rows()` / `note_round()` / `naked_changed()`）——它已 1794 行，
CLAUDE.md 的硬规则是单文件 ≤ 800 行，本轮的净增预算是 ≤ 25 行（G-12）。

判据与文案在纯核心 `discipline_view`；本模块只负责**取数**：从哪个运行时对象读哪个计数器。
两者分开是有意的——判据可以被纯函数单测逐行钉死，取数只能靠会话级集成测试。
"""
from __future__ import annotations

import time

from .discipline_view import (
    DisciplineFacts,
    StrictnessFacts,
    build_discipline_digest,
    naked_backstop,
    strictness_hint,
)
from .open_funnel import OpenFunnelCounters, funnel_wire
from .stats_ext import build_funnel_stats, merge_funnel_stats
from .tradability import stats_wire as tradability_stats_wire
from .tradability import status_wire as tradability_status_wire

# 决策降级理由 → digest 行 key。降级理由是既有 wire（`low_consensus(k/n)` / `low_confidence` /
# `base_rule_*`），此处**只读不改**：它们各自的产生点已有用例守着，在这里重新定义一遍就是第二份口径。
_DEGRADE_PREFIXES = (
    ("low_consensus", "consensus"),
    ("low_confidence", "confidence"),
    ("base_rule_", "baseRuleMaBand"),
)


class DisciplineRuntime:
    """一个会话的纪律自检运行时（纯同步；无 IO、无 await）。

    刻意持**会话引用**而不是十来个散装引用：`sizing` / `cost` / `backstop` / `prior` 这些运行时对象
    在会话生命周期内都是稳定引用，把它们逐个摊进构造签名，只会把 `session.__init__` 的行数预算烧掉，
    而本模块本来就是「会话 → 纯核心」的那一层适配器。读取面**只限**下面 `facts()` 里列出的属性。
    """

    def __init__(self, session, *, preset_id: str = "off") -> None:
        self._session = session
        self.preset_id = preset_id                # 只为落库回答「这次会话开的是哪个预设」
        self._degrades: dict[str, int] = {}
        self._last_naked: bool | None = None      # None = 还没判过（首次判定不算「翻转」）
        # 轴 C：开仓漏斗的会话级累计计数。**住在本门面而不是 session**——它与 13 行自检是同一件事
        # 的两面（「哪些闸开着」与「它们各拦下了多少次真实开仓意愿」），且 `session.py` 已 1819 行、
        # 本轮的净增预算是 ≤ 22 行（G-19b）。
        self.funnel = OpenFunnelCounters()
        # 轴 A：会话启动断面的可交易性只读快照（`manager.start` 算一次，**启动后不变**）。
        self.tradability: dict | None = None

    # ---------- 喂数 ----------
    def note_intent(self, action: str, degrade: str | None) -> None:
        """第一阶段决策的收口：记开仓漏斗的**分母**（门禁后的动作已被洗成 hold，那里数永远是 0）。"""
        self.funnel.note_intent(action, degrade)

    def note_tradability(self, wire: dict | None) -> None:
        """装入启动断面（与启动回执**是同一份对象**，同一 `asOfTs`，[P1-10]）。"""
        self.tradability = wire

    def note_round(self, degrade: str | None) -> None:
        """记一轮决策的降级理由（共识 / 置信度 / 基础策略三条闸的 `firedCount` 唯一来源）。

        这三条闸的纯核心只返回降级理由字符串、不自带计数器；在**决策轮的唯一收口**上按前缀归类，
        比在三个纯核心里各加一个计数器更不容易漂移（且回测侧共用那些纯核心，加计数器会污染它们）。

        轴 C 的漏斗归类**同挂这个唯一收口**：此处的 `degrade` 已是 `degrade or gate_reason` 的归并
        结果，即「第一个拦下它的层」。`note_block` 自己会判「本轮记没记过 intent」——平仓侧被
        `horizon` / `exchangeBackstop` 拦下的轮次一律不进分子（[P0-3]，否则漏斗算术上不闭合）。
        """
        self.funnel.note_block(degrade)
        if not degrade:
            return
        for prefix, key in _DEGRADE_PREFIXES:
            if degrade.startswith(prefix):
                self._degrades[key] = self._degrades.get(key, 0) + 1
                return

    # ---------- 只读出口 ----------
    def rows(self) -> list[dict]:
        """13 行固定顺序的纪律自检 wire（`status.disciplines` 与 `group.discipline.rows` 同源）。"""
        return build_discipline_digest(self.facts())

    def hint(self, rows: list[dict]) -> str | None:
        """[P1-9 / 轴 C] 对症自检提示：一堆闸武装着却一单都没开过 ⇒ 点名**谁在拦、动哪个旋钮**。

        开仓成交数按 `side in ("long","short")` 数——与 `OpenFunnelCounters.note_filled` 的判据
        **共用同一条**，绝不另写（两处口径一旦分叉，漏斗与提示会给出互相矛盾的结论）。
        取数面仍只在本函数里：判据全在 `discipline_view.strictness_hint`（纯核心）。
        """
        executor = self._session.executor
        opens = sum(1 for t in executor.trades if t.get("side") in ("long", "short"))
        return strictness_hint(rows, StrictnessFacts(
            rounds_completed=self._session.rounds_completed, opens=opens,
            funnel=self.funnel_wire(), tradability_level=self._tradability_level()))

    def funnel_wire(self) -> dict | None:
        """开仓漏斗 wire（`status.openFunnel` 与落库 `aiStats.openFunnel` 的**同一次**装配）。"""
        return funnel_wire(self.funnel)

    def tradability_status(self) -> dict | None:
        """`status.tradability`：启动断面的瘦身投影（未探测 ⇒ `None` ⇒ 前端整段不渲染）。"""
        return tradability_status_wire(self.tradability)

    def _tradability_level(self) -> str | None:
        """会话启动断面的可交易性 level；未探测 / 老会话 ⇒ `None`（不造数）。"""
        wire = self.tradability
        return wire.get("level") if isinstance(wire, dict) else None

    def naked_changed(self) -> bool:
        """裸仓事实自上次询问以来是否翻转（轴 C [P0-2]：翻转即补推一次 status）。

        首次判定**不算翻转**——会话刚起来时把 `False → False` 当成变化会在每个盯市 tick 上多推一条
        status。返回 True 的那一刻，调用方须立刻推一次 `status`（其 `disciplines.exchangeBackstop`
        行承载事实）。**刻意不发 `error` 事件**：`useAiTradeGroupStream.routeMemberEvent` 没有
        `error` 分支，推了也是丢——发一条没人收的安全告警比不发更坏。
        """
        naked = self.naked
        changed = self._last_naked is not None and naked != self._last_naked
        self._last_naked = naked
        return changed

    @property
    def naked(self) -> bool:
        """当前是否裸仓（判据的唯一真源在 `discipline_view.naked_backstop`）。"""
        settings = self._session.executor.settings
        return naked_backstop(self._backstop_wire(),
                              float(settings.ai_trade_backstop_min_coverage))

    def merge_into(self, ai_stats: dict) -> dict:
        """把纪律观测并入 `metricsJson.aiStats`（§六.2；镜像 `CostRuntime.merge_into` 的调用点合并）。

        [P2-6] 落的是 `disciplineArmedKeys`（字符串数组）而**不只是**一个计数——只存整数，事后回答
        不了「当时开着哪几条」，而那恰恰是校准这组阈值时唯一想问的问题。同一 JSON 列，零额外成本。
        """
        armed = [row["key"] for row in self.rows() if row["armed"]]
        ai_stats["disciplinePreset"] = self.preset_id
        ai_stats["disciplineArmedKeys"] = armed
        ai_stats["disciplineArmedCount"] = len(armed)
        # 轴 A / B / C 的三个落库加法键（tradability-and-open-funnel §6.2）：使历史会话可回答
        # 「上次为什么没开单」与「我当时是在什么条件下开的这一局」。键冲突即抛，绝不静默覆盖。
        return merge_funnel_stats(ai_stats, build_funnel_stats(
            self.funnel_wire(), tradability_stats_wire(self.tradability),
            getattr(self._session._sizing, "counters", None)))

    def facts(self) -> DisciplineFacts:
        """会话运行时 → 纯核心入参。**取数面就是这一个函数**，改读点只改这里。"""
        session = self._session
        return DisciplineFacts(
            settings=session.executor.settings,
            sizing=session._sizing,
            horizon_rule=session.horizon_rule,
            base_rule=session.base_rule,
            profit_rule=session.profit_rule,
            backstop_wire=self._backstop_wire(),
            gate_counts=self._gate_counts(),
            prior_armed=session._prior.coverage_wire() is not None,
            spec_count=len(session._specs),
            # [P0-4] 启动期的事前复核结论（`manager._build_executor` 置入；非 AI 链路恒 False）。
            dust_risk=bool(getattr(session.executor, "dust_risk", False)))

    # ---------- 取数细节 ----------
    def _backstop_wire(self) -> dict | None:
        """交易所侧兜底核实 wire；空仓 / 未装配 ⇒ None（`BackstopRuntime.wire` 自己判定）。"""
        return self._session._backstop.wire(time.time())

    def _gate_counts(self) -> dict:
        """各闸本会话累计触发次数。**算不出的键一律缺席**（不填 0 冒充「一次都没触发」）。"""
        session = self._session
        sizing = session._sizing
        counters, brake = sizing.counters, sizing.brake_counters
        counts: dict[str, int] = dict(self._degrades)
        counts["edgeGate"] = int(session._cost.attribution.stats()["edgeGate"]["veto"])
        counts["volSizing"] = int(counters.scaled_rounds)
        counts["volSizingDustSkips"] = int(getattr(session.executor, "dust_skips", 0))
        counts["atrStop"] = int(counters.atr_stop_rounds)
        counts["drawdownBrake"] = int(brake.slow_rounds + brake.halt_rounds)
        counts["lossStreak"] = int(brake.cooldown_hits)
        counts["exposureCap"] = int(counters.exposure_unreachable_rounds)
        counts["profitGuard"] = int(session.profit_guard_exits)
        counts["horizon"] = int(sum(session._horizon.vetoes.values()))
        counts["exchangeBackstop"] = int(session._horizon.backstop_vetoes)
        return counts
