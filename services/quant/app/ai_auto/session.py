"""AiTradeSession —— AI 自动交易会话（ai-auto-trading §2.1 / §2.6）。

镜像 `LiveAutoSession` 的逐根驱动（OKX 公共 WS + 暖机 priming + 确认根流水线 + monitor 循环 +
reconcile + 会话级止盈止损 + flatten/stop + WS 扇出），但把「单策略信号 → executor.apply」替换为
「**多策略信号板（每策略影子 SimBroker 回放，P0-1）→ AI 决策器综合研判 → 映射 Signal → 同一个
LiveExecutor 守护执行**」，并按用户设定轮数做滚动摘要式上下文压缩。

并发安全（本方案相对 live-auto 的新增关键点）：
- `_exec_lock`（P1-1）：串行化**一切** executor 写路径（决策 apply、会话止盈止损平仓、stop flatten、
  reconcile 的 projected 写回），杜绝长 LLM 决策轮与 5s monitor 循环并发改 `projected`。
- `_deciding`（P1-2）：in-flight 守卫。决策轮以后台任务运行；上一轮未完成时新确认根到来则本轮跳过
  （推 `decision{skipped:"decision_in_flight"}`），不排队、不并发。影子 broker 每根仍同步步进
  （快，无 LLM），仅 LLM 决策被守卫。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.metrics import _empty_metrics
from ..live_auto.executor import LiveExecutor
from ..live_auto.explain import build_atomic_explain, build_profit_guard_explain
from ..live_auto.resting import resting_payload
from ..okx.rest_client import get_rest_client
from ..okx.types import BAR_SECONDS, Candle
from ..okx.ws_client import OKXWSClient
from ..paper.strategy_factory import PaperStrategySpec
from ..strategies.base import Signal
from .backstop_repair import (RepairRuntime, repair_now_from_executor,
                              rule_from_settings as repair_rule_from_settings, schedule_repair)
from .base_rules import MaBandRule, apply_ma_band_gate, build_ma_band, gate_status
from .cost_runtime import CostRuntime
from .decider import AiTradeDecider, TradeDecision
from .exchange_backstop import BackstopRuntime, schedule_from_executor
from .exit_attribution import classify_live
from .hold_horizon import HorizonFacts, HorizonRule, gate_status as horizon_gate_status
from .horizon_acceptance import ai_stats_keys, build_bundle
from .horizon_runtime import HorizonRuntime, build_horizon_facts
from .market_context import build_market_context
from .market_digest import build_market_digest
from .market_window import MarketWindow, WindowOptions
from .multi_timeframe_context import build_multi_timeframe_context
from .order_plan import decision_to_order_plan
from .prior_runtime import PriorRuntime
from .profit_cooldown import apply_profit_cooldown, build_cooldown_view
from .profit_guard import (
    STATUS_UNAVAILABLE,
    ProfitGuardRule,
    evaluate_profit_guard,
    safe_atr,
)
from .profit_guard_text import describe_exit_outcome
from .profit_guard_runtime import (
    ProfitGuardRuntime,
    SessionReadings,
    build_position_facts,
)
from .prompts import DECISION_TOOL_NAME
from .reflection import applied_shrink, apply_reflection, build_reflection_note
from .signal_preview import PreviewRequest, PreviewRunner
from .signals import (
    Consensus,
    StrategySignalView,
    apply_consensus_gate,
    compute_consensus,
    decision_to_signals,
    evaluate_signal_board,
    make_shadow_broker,
)
from .sizing_runtime import SizingRuntime
from .trace import (
    _TRACES_MAX,
    TraceEmit,
    apply_edge_gate_trace,
    apply_horizon_trace,
    apply_sizing_trace,
    base_rule_event,
    edge_gate_event,
    edge_gate_unavailable_event,
    horizon_event,
    horizon_released_event,
    new_round_trace,
    reflection_start_event,
    reflection_verdict_event,
    sizing_event,
    trace_event,
)
from .turnover_discipline import rule_from_horizon as turnover_rule_from_horizon
from .wake_reason import WakeReadings
from .wake_runtime import InjectionContext, WakeRuntime

_SNAPSHOT_EQUITY_MAX = 1000
_DECISIONS_MAX = 50
# ai-trade-stats-dashboard §3.3：finalize 时权益快照下采样上限（≤此值原样，超出等距抽样保留首尾），
# 避免长会话 metrics_json 膨胀（R-8）。决策摘要沿用 _DECISIONS_MAX（=50）。
_EQUITY_DIGEST_MAX = 500
# 研判思维链有界摘录长度（ai-trade-reasoning-chain §2.5，RC-2/RC-3）：进 journal（摘要素材）与 decision
# 事件（_decisions 环形 + WS snapshot 重放）的一律为此摘录；完整全文只走 agent_trace 流与 _traces(≤20)。
_ANALYSIS_EXCERPT_CHARS = 400
# 【基础策略】通道连续不可用告警阈值（ai-trade-base-strategy-ma-band §2.6/P0-4）：连续这么多轮算不出
# 通道即追发一条 error 事件，使「为什么一直不开单」在 UI 上自解释——静默的保守 = 用户眼中的故障。
_BAND_UNAVAILABLE_ALERT_ROUNDS = 3
# 最小 edge 门禁连续不可用告警阈值（§3.7）：本层 fail-open，故阈值比铁律层宽——连续这么多轮算不出
# 粗估 edge 才追一条 error，使「门禁其实一直没生效」在 UI 上自解释（静默的降级同样会被当成故障）。
_EDGE_UNAVAILABLE_ALERT_ROUNDS = 20
# 持仓周期层连续解除告警阈值（holding-horizon §4.4）：本层同为 fail-open，阈值同宽——连续这么多轮
# 「限制已解除」才追一条 error，使「锁其实一直开着」在 UI 上自解释。
_HORIZON_RELEASED_ALERT_ROUNDS = 20
# 打 `explain.meta.wakeKind` 的成交类型白名单（ai-trade-wake-aware-context §2.9）：只有 AI 决策**直接**
# 产生的成交才归因到唤醒来源。保护层平仓 / 会话止损 / 外部干预由确定性层触发、与唤醒无关，打上去会让
# 「按唤醒来源切胜率」这张表整体失真——而那正是本条要解决的问题。
_WAKE_ATTRIBUTED_KINDS = ("atomic", "reverse")

logger = logging.getLogger(__name__)

_ACTION_LABEL = {"open_long": "开多", "open_short": "开空", "close": "平仓", "hold": "观望",
                 "cancel_order": "撤单"}
# 决策动作 → `Signal.side`（M-6：原为 `_ai_explain` 内的裸下标，新动作当轮即 KeyError）。
_ACTION_SIDE = {"open_long": "long", "open_short": "short", "close": "close"}


class AiTradeSession:
    """一个 (multi-strategy, inst, bar) 的 AI 自动交易会话（单活跃独占，真实资金）。"""

    def __init__(self, session_id: str, run_id: int, specs: list[PaperStrategySpec],
                 inst_id: str, bar: str, executor: LiveExecutor, decider: AiTradeDecider, *,
                 take_profit_usd: float | None = None, stop_loss_usd: float | None = None,
                 priming: bool = True, priming_bars: int = 300,
                 context_refresh_rounds: int = 10, wake_interval_s: float = 0.0,
                 base_rule: MaBandRule | None = None,
                 profit_rule: ProfitGuardRule | None = None,
                 prior_snapshot: dict | None = None,
                 sizing: SizingRuntime | None = None,
                 horizon_rule: HorizonRule | None = None):
        self.session_id = session_id
        self.run_id = run_id
        self._specs = specs
        self.strategy_ids = [s.strategy_id for s in specs]
        self.strategy_names = [s.strategy_name for s in specs]
        self.inst_id = inst_id
        self.bar = bar
        self.executor = executor
        self.decider = decider
        self.take_profit_usd = take_profit_usd
        self.stop_loss_usd = stop_loss_usd
        self.priming = priming
        self.priming_bars = priming_bars
        self.context_refresh_rounds = context_refresh_rounds
        # 墙钟定时唤醒周期（秒；0=关闭）。默认 0 → 定时唤醒关闭，既有直接构造会话的单测行为逐字不变
        # （与 position_step_pct=0.0 同零回归范式，仅经启动请求显式开启）。
        self.wake_interval_s = wake_interval_s
        # 【基础策略】MA 通道开单铁律（ai-trade-base-strategy-ma-band §2.5）。默认 None=不装配 ⇒ payload
        # 不带 baseRules 键、prompt 逐字节不变、门禁直通、轨迹零条（既有直接构造会话的单测零回归）；
        # 生产装配由 manager 从 API 层解析结果显式传入（默认开启只在 API 层解析，P1-5）。
        self.base_rule = base_rule
        self._band_unavailable_streak = 0     # 连续通道不可用轮数（§2.6 告警升级）
        # 利润保护层（ai-trade-profit-protection §2.8）。默认 None=不装配 ⇒ payload 不带 profitGuard 键、
        # prompt 逐字节不变、盯市不判定、零事件（既有直接构造会话的单测零回归，与 base_rule 同款垫片）；
        # 生产装配由 manager 从 API 层解析结果显式传入（默认开启只在 API 层解析）。
        self.profit_rule = profit_rule
        # F0（adaptive §2.0）：状态 / 快照 / 计数 / payload 注入全部下沉 `ProfitGuardRuntime`（纯同步）；
        # 会话只留三个 async 编排点（_maybe/_apply/_report_profit_exit）与时钟读数打包。
        self._guard = ProfitGuardRuntime(profit_rule)
        self._atr_px: float | None = None     # 确认根 ATR（F1）；None ⇒ 回吐距离上下界不生效
        # 成本记账与最小 edge 门禁（§3.10）：状态 / 快照 / 计数全部下沉 `CostRuntime`（镜像 F0 把
        # 利润保护下沉 `ProfitGuardRuntime` 的同款范式），会话只留薄封装调用。
        # 交易周期模式（holding-horizon §2.4）：同款下沉范式；未装配 ⇒ 无 tradingHorizon 键、prompt
        # 逐字回退、门禁直通、零轨迹。前瞻窗口由契约派生并**同时**喂成本层与先验层（§2.6 要求同源）。
        self.horizon_rule = horizon_rule
        # `net_mode`：退出来源账本的净额还原口径，与 `AttributionLedger` **同一条规则**（轴 B）。
        self._backstop = BackstopRuntime(executor.settings, executor.creds, inst_id)
        self.started_ts = int(time.time())
        self._repair = RepairRuntime(
            executor.settings,
            repair_rule_from_settings(executor.settings,
                                      getattr(horizon_rule, "backstop_repair_mode", None),
                                      horizon_armed=horizon_rule is not None),
            executor, inst_id, session_key=f"{inst_id}|{self.started_ts}",
            verify_hook=self._backstop.force_next)
        self._horizon = HorizonRuntime(
            horizon_rule, net_mode=bool(executor.settings.ai_trade_net_pnl_enabled),
            turnover_rule=turnover_rule_from_horizon(horizon_rule, executor.settings),
            backstop=self._backstop, repair=self._repair)
        self._round_horizon: dict | None = None      # 本轮 gate 快照（decision 事件与判定同源）
        hold_bars = None if horizon_rule is None else horizon_rule.expected_hold_bars
        self._cost = CostRuntime(inst_id, executor.settings, executor.fee_ledger,
                                 executor.creds, allow_limit=decider.allow_limit,
                                 expected_hold_bars=hold_bars)
        # 信号先验层（signal-prior-and-ev-gate §3.1）：同款下沉范式；总闸①关或快照缺席 ⇒ payload
        # 无 signalPriors 键、prompt 逐字节回退、EV 分子回退 ATR 粗估（既有单测零回归）。
        self._prior = PriorRuntime(prior_snapshot, executor.settings, horizon_bars=hold_bars)
        # 仓位 / 刹车层（vol-sizing §3.1）：同款下沉；未装配 ⇒ 零动作、无 sizingPlan 键、risk 从不被赋值。
        self._sizing = sizing or SizingRuntime()
        # P0-1：会话级单调持仓序号——`_dispatch` 观察到任一 `side=="close"` 成交即 +1。epoch 身份的一半，
        # 没有它，「保护平仓 → 同一 tick 内同向重开」的新仓会继承旧峰值并被当场强平（真实资金）。
        self._position_seq = 0

        self.state = "running"
        self.stop_reason: str | None = None
        self.rounds_completed = 0
        self.flatten_result: dict | None = None
        self.flatten_failed = False
        self.residual: dict | None = None
        self.degraded = False

        # 唤醒感知上下文（wake-aware-context §2.6）：必须**先于** `MarketWindow` 构造——窗口的
        # `WindowOptions.tactical_bars` 要吃 `self._wake.tactical_bars`，战术集合的唯一真源在此。
        self._wake = WakeRuntime(bar, wake_interval_s, executor.settings)
        self._window = MarketWindow(
            inst_id,
            bar,
            specs,
            priming_bars,
            options=WindowOptions(
                context_enabled=executor.settings.ai_trade_multi_timeframe_context,
                tactical_bars=self._wake.tactical_bars,
                tactical_window_bars=executor.settings.ai_trade_tactical_window_bars,
                tactical_min_refresh_s=executor.settings.ai_trade_tactical_min_refresh_s,
                tactical_fetch_timeout_s=executor.settings.ai_trade_tactical_fetch_timeout_s),
        )
        self._shadows = [make_shadow_broker() for _ in specs]
        self._exec_lock = asyncio.Lock()      # P1-1：串行化一切 executor 写
        self._deciding = False                # P1-2：决策轮 in-flight 守卫
        self._decision_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._last_candle_wall = 0.0
        # 判定价墙钟（ai-trade-profit-protection §2.4 第 0b 条）：**每次 `_last_live_px` 被写入**即刷新
        # （含未确认根推送），故它是「保护层判定价的真实年龄」。刻意不复用 `_last_candle_wall`——后者只在
        # 确认根落地时更新，15m 周期下每根之间会有近 15 分钟不更新，用它做新鲜度判据会让保护层在绝大多数
        # 时间里恒为 unavailable，正好废掉「不等收线即生效」这个核心目标（详见 spec §十一）。
        self._last_px_wall = 0.0
        # 墙钟定时唤醒复用态（wake-interval §五）：最近一次确认根信号板快照 + 其窗口索引 + 最近实时价 +
        # 最近一轮决策开始的墙钟（定时器基准，任一轮决策开始即刷新以与 K 线唤醒合流去重）。
        self._last_board: list[StrategySignalView] | None = None
        self._last_bar_index = 0
        self._last_live_px: float | None = None
        self._last_decision_started_wall = 0.0
        self._live_row: dict | None = None      # 未确认根临时行（轴 C 预演的唯一输入行，不入窗）
        self._preview = PreviewRunner(session_id, executor.settings)   # 轴 C 异步编排（总闸默认关）
        # 空仓重复唤醒守卫态（hold-inertia-fix F4c）：最近一次**决策轮启动时**的确认根索引 / 参考价
        # （None=尚无决策轮 → 守卫直通）+ 被抑制的唤醒计数 + 计数节流墙钟（R-02：每唤醒周期至多计一次）。
        self._last_round_bar_index: int | None = None
        self._last_round_px: float | None = None
        self.wake_skips = 0
        self._last_skip_counted_wall = 0.0
        # 连续真实观望计数（hold-inertia-observability N2）：第一阶段连续 hold 轮数 + 会话级全程最大值
        # （max 供 finalize 落 aiStats.maxHoldStreak——环形样本算不出全程最大值，与 wake_skips 同为会话级直取）。
        self.hold_streak = 0
        self.max_hold_streak = 0
        self._stale_degraded = False
        self._degraded_seen = False
        self._last_status_side = executor.projected
        self._warmed_bars = 0

        self._equity: deque[dict] = deque(maxlen=_SNAPSHOT_EQUITY_MAX)
        self._peak_equity = executor.budget_usd
        self._decisions: deque[dict] = deque(maxlen=_DECISIONS_MAX)
        self._last_decision: dict | None = None
        # 决策认知轨迹（ai-auto-trading-agent-trace §3.3）：瞬态环形，仅内存 / 快照回放。
        self._traces: deque[dict] = deque(maxlen=_TRACES_MAX)
        self._round_trace: dict | None = None   # 当前在飞轮次（原地累积；snapshot 出口防御性拷贝冻结）
        self._subscribers: set[asyncio.Queue] = set()
        self._ws: OKXWSClient | None = None

    @property
    def poll_interval_s(self) -> int:
        return self.executor.settings.live_auto_poll_interval_s

    # F0：计数迁入 `ProfitGuardRuntime` 后，既有读点（finalize 的 aiStats / 用例）零改动地继续走这里。
    @property
    def profit_guard_exits(self) -> int:
        """**实际产生了平仓成交**的保护触发次数（事件级，P1-1）。"""
        return self._guard.exits

    @property
    def profit_guard_armed_rounds(self) -> int:
        """快照曾 armed/breakeven_armed 的轮数（轮级，P2-2；与 exits 不可换算）。"""
        return self._guard.armed_rounds

    # ---------- fan-out ----------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def _push(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _dispatch(self, events: list[dict]) -> None:
        """统一分发 executor 事件：trade 注入 explain.meta + best-effort 落库，再 WS 扇出。

        P0-1：本方法是**全部** executor 事件的唯一收口，故 `_position_seq` 在此自增——`side == "close"`
        覆盖全部五条平仓路径且无一遗漏（保护平仓 / LLM 平仓 / 反手腿 / 限价平仓采纳 / OCO 与外部干预的
        合成成交，三处均经 `_make_trade(ts, "close", …)` 产出）。而**开仓部分成交**发的是 long/short
        的 trade，`seq` 不动 ⇒ 同一 epoch 的 `peak_px` 正确保留，恰是 §2.3 想要的语义。
        """
        for event in events:
            if event.get("type") == "trade" and isinstance(event.get("data"), dict):
                trade = event["data"]
                if trade.get("side") == "close":
                    self._position_seq += 1     # 任一平仓成交 = 一个持仓周期终结
                    if "peakPnlUsd" not in trade:      # 绝不用 setdefault（P2-7：恒求值实参 ⇒
                        # 键已存在时也会把峰值消费掉，让下一笔平仓静默拿到 None）
                        trade["peakPnlUsd"] = self._guard.take_peak_pnl()
                    self._cost.record_close(trade)      # §3.8：往返归因同挂这个唯一收口
                    self._sizing.note_close(trade, now_wall=time.time())   # 连亏计数同挂唯一收口
                    self._horizon.note_close(trade, time.time())   # 真实持仓时长样本同挂唯一收口
                else:   # 开仓成交：锁定 epoch 的 opened_wall（部分成交同一 epoch ⇒ 内部幂等不重置）
                    self._horizon.note_open(trade, time.time(), seq=self._position_seq)
                    self._repair.note_epoch(self._position_seq)   # 新持仓周期 ⇒ 补挂配额清零
                    self._backstop.force_next()   # 随单 OCO 是否真的挂上，是开仓后最该立刻知道的事
                    self._cost.note_open(trade, seq=self._position_seq, now_wall=time.time())
                self._sizing.note_executor(self.executor, self._current_px())   # [P1-8] 敞口簿
                self._inject_explain_meta(trade)
                await self._persist_trade(trade)
            await self._push(event)

    def _inject_explain_meta(self, trade: dict) -> None:
        explain = trade.get("explain")
        if not isinstance(explain, dict):
            return
        meta = dict(explain.get("meta") or {})
        meta.setdefault("source", "ai")
        meta.setdefault("instId", self.inst_id)
        meta.setdefault("bar", self.bar)
        meta.setdefault("barTs", trade.get("ts"))
        if trade.get("clOrdId"):
            meta.setdefault("clOrdId", trade["clOrdId"])
        if trade.get("ordId"):
            meta.setdefault("ordId", trade["ordId"])
        self._cost.apply_explain_meta(meta, trade)   # A0-1：记账关闭即空转 ⇒ meta 逐字回退
        # §2.9 唤醒来源归因：让「我赚钱的那些交易是哪种唤醒触发的」这个问题事后可用数据回答。
        # 总闸关闭 ⇒ `last_round_kind` 恒 None ⇒ 本键整体缺席（老数据同样缺席，查询侧按「未知」处理）。
        if explain.get("kind") in _WAKE_ATTRIBUTED_KINDS and self._wake.last_round_kind:
            meta.setdefault("wakeKind", self._wake.last_round_kind)
        # 退出来源归因（horizon-acceptance 轴 B）：只给平仓成交打，开仓腿无「退出来源」可言。
        # 打完标后落库 / WS 扇出 / 历史查询 / CSV 导出全部自动带上，无需任何额外通路。
        # 总闸关 ⇒ 整键缺席（老数据同样缺席，查询侧一律按 "unknown" 处理）。
        if (trade.get("side") == "close"
                and self.executor.settings.ai_trade_exit_attribution_enabled):
            meta.setdefault("exitSource", classify_live(explain))
        explain["meta"] = meta

    async def _persist_trade(self, trade: dict) -> None:
        try:
            sm = get_sessionmaker()
            async with sm() as s:
                await repo.append_live_trade(s, self.run_id, trade)
        except Exception as exc:  # noqa: BLE001 —— best-effort 审计落库，失败不阻塞热路径
            logger.warning("append_live_trade failed (run_id=%s): %s", self.run_id, exc)

    # ---------- lifecycle ----------
    async def start(self) -> None:
        """暖机（仅热指标 + 种入影子立场，不下任何真单）后订阅实时 WS 与 monitor 循环。"""
        await self._prime()
        self._ws = OKXWSClient(self.inst_id, self.bar, self.feed_candle)
        self._ws.start()
        now = time.time()
        self._last_candle_wall = now
        self._last_px_wall = now          # 暖机末价即本轮判定价，与 _last_candle_wall 同一起点
        # 首次定时唤醒在启动后约 wake_interval_s 触发（而非 t=0 立刻，R-8）。
        self._last_decision_started_wall = now
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        for alert in self._prior.alerts:   # 先验静默降级会被当成故障（R-5）：启动即如实推一条
            await self._push(alert)
        await self._push({"type": "status", "data": self._status_payload()})

    async def _prime(self) -> None:
        """预填窗口 + 逐根回放种入各策略影子（每策略独立，P0-1）；真实决策从首个实时确认根起。"""
        await self._window.prefill_htf(self.priming_bars if self.priming else 60)
        await self._window.prefill_tactical()    # 战术窗口预填（取数失败留空，绝不阻塞暖机）
        rows = await self._window.fetch_priming_rows(self.priming_bars if self.priming else 60)
        for row in rows:
            self._window.prime_append(row)
        self._warmed_bars = len(self._window.candles)
        if not self.priming or not rows:
            return
        frames = self._window.build_strategy_frames()   # 各策略在整段暖机窗口上的因果帧
        board: list[StrategySignalView] | None = None
        n = len(self._window.candles)
        for i in range(n):
            board = evaluate_signal_board(self._specs, self._shadows, frames, i)  # 仅种入影子
            if i % 50 == 0:
                await asyncio.sleep(0)                   # 周期让出事件循环，避免暖机长阻塞
        # 捕获暖机末根返回视图供定时唤醒复用（不额外多调一次 evaluate_signal_board，避免末根被双重步进）：
        # 首次定时唤醒可在会话启动 ~wake_interval_s 后即触发，无需干等第一根实时确认 K 线收盘。
        self._last_board = board
        self._last_bar_index = n - 1
        self._last_live_px = self._ref_px()

    async def stop(self, reason: str = "user_stop") -> dict:
        """平掉自管持仓 + 停 WS/monitor/决策 + 落 Run 终态（幂等：非 running 直接返回上次结果）。"""
        if self.state != "running":
            return self.flatten_result or {"ok": True, "closedSz": 0}
        self.state = "stopped"
        self.stop_reason = reason
        self._cancel_task("_monitor_task")
        self._cancel_task("_decision_task")
        self._backstop.close()   # 在途核实回来后不再写状态、不再推事件（§2.3.1 约束 5）
        self._repair.close()     # 同上：在途补挂回来后不再写状态、不再推事件
        if self._ws is not None:
            await self._ws.stop()
        ref_px = self._ref_px()
        ts = self._ref_ts()
        try:
            async with self._exec_lock:
                self.flatten_result = await self.executor.flatten(ref_px, ts)
        except Exception as exc:  # noqa: BLE001 —— 平仓失败如实记录，仍完成停止流程
            self.flatten_result = {"ok": False, "message": str(exc)}
        await self._handle_flatten_result(ts)
        await self._cost.reconcile_tick(          # 轴 C：终态对账（硬超时，永不判 drift）
            time.time(), window_start_ts=self.started_ts,
            trades=self.executor.trades, push=self._push, final=True)
        await self._finalize_run(ts)
        await self._push({"type": "status", "data": self._status_payload()})
        return self.flatten_result

    def _cancel_task(self, attr: str) -> None:
        """取消命名后台任务，但绝不取消自身（会话止损在 monitor/决策任务内触发 stop，R-2）。"""
        task = getattr(self, attr, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        setattr(self, attr, None)

    async def _handle_flatten_result(self, ts: int) -> None:
        result = self.flatten_result or {}
        # §2.8：flatten 先撤挂单——撤单 / 「最后一刻成交」的采纳事件须如实扇出（缺键即无挂单，
        # 与本特性合入前逐字相同）。
        if result.get("events"):
            await self._dispatch(result["events"])
        if result.get("trade"):
            await self._dispatch([{"type": "trade", "data": result["trade"]}])
        if result.get("ok"):
            return
        self.flatten_failed = True
        self.residual = result.get("residual")
        await self._push({"type": "error", "data": {
            "ts": ts, "code": "AI_TRADE_FLATTEN_FAILED",
            "message": "AI 自动交易停止时平仓失败，请立即到下方实盘面板手动平仓",
            "residual": self.residual}})

    def _ref_px(self) -> float:
        c = self._window.candles
        return float(c[-1]["c"]) if c else self.executor.entry_price

    def _ref_ts(self) -> int:
        c = self._window.candles
        return int(c[-1]["ts"]) if c else self.started_ts

    # ---------- monitor 循环 ----------
    async def _monitor_loop(self) -> None:
        try:
            while self.state == "running":
                await asyncio.sleep(self.poll_interval_s)
                if self.state != "running":
                    break
                await self._monitor_tick()
        except asyncio.CancelledError:
            pass

    async def _monitor_tick(self) -> None:
        """单轮盯市：reconcile（经 _exec_lock）+ 盯市 + staleness + 会话止盈止损（经 _exec_lock）。"""
        now_ts = int(time.time())
        mark = await self._latest_mark_px()
        async with self._exec_lock:
            events = await self.executor.reconcile(mark, now_ts)
        await self._dispatch(events)
        await self._push_status_if_side_changed()
        await self._emit_equity(mark, now_ts)
        await self._emit_position(mark)
        await self._check_staleness(now_ts)
        self._degraded_seen = (self._degraded_seen or self.executor.degraded
                               or self._stale_degraded)
        # 利润保护先于会话止盈（§2.5）：一次保护性平仓可能正好把会话累计盈亏推过 TP，随后
        # `_maybe_session_exit` 自然停会话；反序则会先停会话、保护层再无机会锁住这笔利润。
        await self._maybe_profit_exit(now_ts)
        # 持仓周期快照随盯市推进（面板 chip 的倒计时在无决策轮时也要动；纯同步、不判定、不平仓）。
        self._horizon.refresh(self._horizon_facts(time.time()))
        await self._expire_profit_cooldown()
        await self._maybe_session_exit(mark, now_ts)
        # §3.1：网络 IO 只跑在盯市 tick 上；三条刷新（资金费率 / 官方费率 / 交易所对账）离开
        # tick 的关键路径跑在后台单飞任务里——串行 await 会把 5s 盯市周期翻倍（I-1）。
        self._cost.schedule_maintenance(time.time(), window_start_ts=self.started_ts,
                                        trades=self.executor.trades, push=self._push,
                                        mark_px=self._current_px())
        schedule_from_executor(self._backstop, self.executor, now_wall=time.time(),
                               push=self._push)   # 只读、单飞、仅持仓时发（turnover-budget §2.3.1）
        # ★ 交易所侧兜底自愈（backstop-repair §2.2）：状态变化即补推一次 status（[P1-3]）——全量
        # 快照不挂 5s 盯市 tick，而本层的目标用户恰是唤醒周期 0、决策轮以小时计的 trend_hold。
        schedule_repair(self._repair, self._backstop, self.executor,
                        self._session_readings(time.time()), push=self._push,
                        on_change=lambda: self._push({"type": "status",
                                                      "data": self._status_payload()}))
        # 战术窗口刷新**必须**排在定时唤醒之前——否则定时轮拿到的是上一分钟的战术数据，整个特性
        # 在最该生效的路径上空转。三闸门（锚点/配额/单 tick 节流）+ 硬超时保证它不拖停盯市。
        await self._window.maybe_refresh_tactical(now_ts, time.time())
        await self._maybe_signal_flip_wake()     # 轴 C：须在定时唤醒之前（翻转优先于墙钟到点）
        await self._maybe_interval_wake()

    async def _maybe_interval_wake(self) -> None:
        """墙钟定时唤醒（第三触发源，wake-interval §2.2）：满足全部条件时用「最近确认根信号板 + 当前
        实时价」触发一轮标准决策。in-flight/关闭/非运行/行情陈旧/尚无信号板 → 一律不唤醒。

        F4c（hold-inertia-fix）：既有条件通过后、认领 `_deciding` 槽位之前，先过空仓重复唤醒守卫
        （纯同步，守卫与认领之间无 await、无竞态）——空仓+无挂单+无新确认根+价格微动时跳过本次唤醒，
        且**不刷新** `_last_decision_started_wall`：下个 monitor tick（默认 ~5s）重新评估，价格一越过
        阈值或新 K 线到来即恢复唤醒；持仓轮/挂单轮永不跳过（盯市职责不受影响）。

        wake-aware-context §2.5：守卫判据收敛到 `WakeRuntime` —— 先分类（纯同步）再问要不要跳过，
        「跳过」与「prompt 措辞」从此读同一个 `kind`，杜绝两处各算一次。

        轴 A：`_deciding` 判定**下移到计时器之后**，只对「真的到点却撞上在飞轮」的丢弃记账。
        """
        if (self.wake_interval_s <= 0 or self.state != "running"
                or self._stale_degraded or self._last_board is None):
            return
        now = time.time()
        if now - self._last_decision_started_wall < self.wake_interval_s:
            return
        if self._deciding:                    # 轴 A：到点却撞上在飞轮 ⇒ 记待办，不再静默丢弃
            if self._wake.note_interval_drop(now):
                await self._push({"type": "status", "data": self._status_payload()})
            return
        ref_px = self._current_px()           # 纯同步取实时价（不 await）
        wake = self._wake.classify(self._wake_readings("interval", ref_px), now_wall=now)
        if self._wake.should_skip(wake):
            # O1（hold-inertia-observability）：守卫命中期间没有任何事件发出，live 面板的 wakeSkips
            # 只能停在连接时 snapshot 的旧值；计数成功即补推一次 status（频率被 R-02 节流天然封顶：
            # 每唤醒周期至多一条，不新增节流机制）。
            counted = self._count_wake_skip(now)
            self._wake.note_skip(counted)     # 连续跳过计数与 wake_skips 同口径（绝不按 tick 裸计）
            if counted:
                await self._push({"type": "status", "data": self._status_payload()})
            return
        self._deciding = True                 # 同步认领槽位：check→set 间无 await（与 feed_candle 同范式，P1-2）
        self._last_round_bar_index = self._last_bar_index
        self._last_round_px = ref_px
        self._last_decision_started_wall = now
        self._decision_task = asyncio.create_task(
            self._run_decision_round(self._last_board, self._last_bar_index, ref_px,
                                     int(now), wake))

    def _wake_readings(self, trigger: str, ref_px: float) -> WakeReadings:
        """本次唤醒的会话侧断面（executor 与窗口的现成读数；保护快照与 ATR 由运行时回读）。"""
        resting = self.executor.resting
        payload = resting_payload(resting, int(time.time())) if resting is not None else None
        return WakeReadings(
            trigger=trigger, position_side=self.executor.projected,
            has_resting=resting is not None, bar_index=self._last_bar_index, px=ref_px,
            resting_age_s=(payload or {}).get("ageS"))

    def _count_wake_skip(self, now: float) -> bool:
        """守卫命中计数（R-02 节流）：每个唤醒周期至多计数+日志一次。

        守卫命中不刷新计时器（为价格越阈即恢复），「到点」判定此后每个 ~5s monitor tick 恒成立；
        若按 tick 裸计数，3 分钟周期下 1 小时静止行情会虚计 ~720 次（老行为仅 ~20 次唤醒），F5 观测
        口径失真且 debug 日志刷屏。节流后语义 =「被抑制的、原本会发生的唤醒次数」。
        """
        if now - max(self._last_decision_started_wall,
                     self._last_skip_counted_wall) < self.wake_interval_s:
            return False
        self.wake_skips += 1
        self._last_skip_counted_wall = now
        logger.debug("ai-trade interval wake skipped (flat, unchanged data): session=%s total=%d",
                     self.session_id, self.wake_skips)
        return True

    def _update_hold_streak(self, stage1_action: str, stage1_degrade: str | None) -> None:
        """连续真实观望计数（hold-inertia-observability N2）。

        以**第一阶段**（decider.decide 直接返回值，共识门禁/反思之前）为准：
        - 非 hold → 归零（模型已展示交易意愿——即使随后被共识门禁或反思否决，惯性已在第一阶段打破）；
        - hold 且无降级 → +1（模型真实观望）；
        - hold 且有降级 → 不变（既非充分意愿也非真实观望，不计入亦不打断）。该分支覆盖两类：
          llm_error/timeout 等安全回退，以及 low_confidence 风险门禁改写轮——decide() 对置信度
          不达标的开仓意愿返回 ("hold", "low_confidence")（decider.py:359），弱意愿按中性处理，
          **不归零**（R2-03 语义钉，勿按「已展示意愿→归零」走样）。
        """
        if stage1_action != "hold":
            self.hold_streak = 0
        elif stage1_degrade is None:
            self.hold_streak += 1
            self.max_hold_streak = max(self.max_hold_streak, self.hold_streak)

    def _current_px(self) -> float:
        """定时唤醒取实时价：优先最近未确认/确认根实时价（`is not None` 判定，防 0 价异常断面误回落），
        回落到最近确认收盘价（`_ref_px`）。价恒 > 0 时二者等价，此处更防御性。"""
        return self._last_live_px if self._last_live_px is not None else self._ref_px()

    async def _latest_mark_px(self) -> float:
        c = self._window.candles
        if c:
            return float(c[-1]["c"])
        try:
            px = await get_rest_client().get_ticker(self.inst_id)
        except Exception:  # noqa: BLE001 —— 取价失败回落 entry_price（降级，不阻塞盯市）
            px = None
        return float(px) if px is not None else self.executor.entry_price

    async def _check_staleness(self, now_ts: int) -> None:
        if self.state != "running" or self._last_candle_wall <= 0:
            return
        settings = self.executor.settings
        stale_after = BAR_SECONDS.get(self.bar, 60) * settings.live_auto_staleness_factor
        elapsed = now_ts - self._last_candle_wall
        if elapsed <= stale_after:
            await self._clear_stale_if_recovered()
            return
        if settings.live_auto_flatten_on_stale and elapsed > stale_after * 4:
            await self.stop("stale_feed")
            return
        if not self._stale_degraded:
            self._stale_degraded = True
            await self._push({"type": "status", "data": {
                **self._status_payload(),
                "message": "行情连接异常，AI 自动交易暂停响应 K 线（交易所侧止盈止损仍生效）"}})

    async def _clear_stale_if_recovered(self) -> None:
        if not self._stale_degraded:
            return
        self._stale_degraded = False
        await self._push({"type": "status", "data": {
            **self._status_payload(), "message": "行情已恢复，AI 自动交易盯市恢复正常"}})

    # ---------- 核心：每根已确认 K 线 = 一个决策轮 ----------
    async def feed_candle(self, candle: Candle) -> None:
        """OKX WS 回调（也可被测试直接注入）。确认根：步进影子信号板 → 触发一轮 AI 决策。"""
        if self.state != "running":
            return
        if not candle.confirm:
            self._last_live_px = float(candle.c)   # 两根 K 线之间的当前价（供定时唤醒研判最新价位）
            self._last_px_wall = time.time()       # 判定价年龄基准（利润保护新鲜度豁免的唯一真源）
            self._live_row = {"ts": candle.ts, "o": candle.o, "h": candle.h,   # 轴 C 预演输入行
                              "l": candle.l, "c": candle.c, "vol": candle.vol}
            await self._push({"type": "candle", "data": {
                "ts": candle.ts, "o": candle.o, "h": candle.h, "l": candle.l,
                "c": candle.c, "vol": candle.vol, "confirm": False}})
            return
        if self._window.is_duplicate(candle):
            return
        row = self._window.append_confirmed(candle)
        self._last_candle_wall = self._last_px_wall = time.time()
        self._wake.note_new_bar()         # 轴 C：新根 = 新证据，解锁同方向翻转的防抖锁存
        self._live_row = None             # 该根已收线，旧的未确认行不再有意义
        # F1（§2.1.2）：ATR 是波动**尺度**，只在确认根刷新（允许滞后一根）；不可用 ⇒ None ⇒
        # 上下界整体不生效，退化为纯阶梯回吐（与本层 fail-open 极性一致）。
        self._atr_px = safe_atr(self._window.base_df(),
                                self.executor.settings.ai_trade_profit_guard_atr_period)
        self._wake.note_atr(self._atr_px)     # ATR 判据与参照断面共用这一个数（唯一入口）
        self._sizing.note_candles([c["c"] for c in self._window.candles], int(candle.ts))
        decision_as_of_ts = int(candle.ts) + BAR_SECONDS[self.bar]
        await self._window.maybe_refresh_htf(decision_as_of_ts)
        # 锚点闸使其在盯市已刷新时为零成本空转；确认根这条路径保证「新根落地即有最新战术数据」。
        await self._window.maybe_refresh_tactical(decision_as_of_ts, time.time())
        frames = self._window.build_strategy_frames()
        i = self._window.cur_index()
        ref_px = float(row["c"])
        ts = int(row["ts"])
        board = evaluate_signal_board(self._specs, self._shadows, frames, i)  # 步进影子（快，无 LLM）
        self._last_board = board                 # 存最近确认根信号板快照 + 索引 + 实时价（供定时唤醒复用）
        self._last_bar_index = i
        self._last_live_px = ref_px
        await self._push({"type": "candle", "data": {**row, "confirm": True}})
        if self._deciding:                       # P1-2：上一轮未完成→跳过本轮，不并发
            self._wake.note_bar_drop(time.time())    # 轴 A：先记待办（轮末补跑），再推自解释事件
            await self._emit_decision_skipped(ts, board)
            return
        self._deciding = True
        self._last_round_bar_index = i           # F4c：记录本决策轮启动断面（根索引 + 参考价）
        self._last_round_px = ref_px
        now_wall = time.time()
        self._last_decision_started_wall = now_wall   # 任一轮决策开始即刷新计时器，与定时唤醒合流去重
        wake = self._wake.classify(self._wake_readings("candle", ref_px), now_wall=now_wall)
        self._decision_task = asyncio.create_task(
            self._run_decision_round(board, i, ref_px, ts, wake))

    async def _run_decision_round(self, board: list[StrategySignalView], i: int,
                                  ref_px: float, ts: int, wake: dict) -> None:
        """一个决策轮：装配输入 → AI 决策 → 映射 Signal 守护执行 → 记录/摘要 → 推事件（含认知轨迹）。

        `wake`（wake-aware-context §2.7）承载本轮唤醒断面，其中 `wake["trigger"]` 即原 `trigger` 形参
        的取值（wire 逐字不变）。透传到 payload / round_start 轨迹 / decision 事件，供 prompt 与前端
        区分唤醒来源；决策安全性不依赖它。
        """
        round_no = self.rounds_completed + 1     # RV-5：try 之前绑定，except 可安全引用
        try:
            payload = self._build_decision_payload(round_no, ts, ref_px, board, wake)
            # 断面冻结须在 payload 之后：`changeSinceLastRound` / `closedBars` 参照的是**上一**轮。
            self._wake.note_round_start(wake, px=ref_px, bar_index=i,
                                        as_of_ts=self._decision_as_of_ts(ts, wake),
                                        now_wall=time.time())
            consensus = compute_consensus(board)                 # E2：每轮只算一次（供门禁 + wire）
            payload["consensus"] = self._consensus_wire(consensus)
            await self._begin_round_trace(round_no, ts, ref_px, payload, board)  # RV-7：consensus 之后
            decision, degrade = await self.decider.decide(
                payload, emit=self._make_trace_emit(round_no, ts))
            # N2（hold-inertia-observability）：以第一阶段原始输出立即更新连续观望计数——须在共识门禁
            # 与反思之前（此时的 decision/degrade 正是第一阶段输出，惯性以模型真实意愿为准）。
            self._update_hold_streak(decision.action, degrade)
            # 同上落点（§2.6 c）：盈利持有链条同样以第一阶段真实意愿为准，据本轮保护快照的浮盈正负推进。
            self._update_profit_hold_streak(decision.action, degrade,
                                            payload.get("profitGuard"))
            # C1 §2.2.4：把模型本轮承诺的退出线记进运行时棘轮——同以**第一阶段**决策为准（门禁 / 反思
            # 之前）：门禁只会把 open_* 降级成 hold、绝不凭空造出 hold_exit_px，故用真实意愿采集最纯。
            self._record_profit_commitment(decision.action, decision.hold_exit_px,
                                           payload.get("profitGuard"))
            # ★【基础策略】铁律门禁（ai-trade-base-strategy-ma-band §2.5）：必须落在 decide **之后**
            # （重试循环只认 _map_decision 的 degrade，落进去会每轮白烧 LLM，P1-1）、共识门禁与反思
            # **之前**（被否轮此时已是 hold，反思对 hold 本就直通 ⇒ 自动零 LLM 成本，P1-2）。
            # 连续确认计数须在**任何门禁之前**记（§2.5 硬约束 4）：记门禁后的动作，一次 MA 通道否决
            # 就会把 close 意图的连续性洗掉、G2 永远数不到 2。
            self._horizon.note_intent(decision.action, degrade, now_wall=time.time(),
                                      position_side=self.executor.projected)
            decision, degrade = await self._apply_base_rule_gate(
                round_no, ts, payload, decision, degrade)
            # ★ 持仓周期铁律门禁（holding-horizon §2.5）：基础策略**之后**、冷却**之前**。
            decision, degrade = await self._apply_horizon_gate(round_no, ts, decision, degrade)
            # ★ 利润保护冷却门禁（adaptive §2.2.2）：铁律后、共识前（见 _apply_profit_cooldown_gate）。
            decision, degrade = self._apply_profit_cooldown_gate(decision, degrade)
            # ★ 换手纪律 G4/G5 与兜底覆盖 G6（turnover-budget §2.2.5）：冷却后、仓位/刹车前。
            decision, degrade = self._horizon.turnover_gate(
                decision, degrade, position_side=self.executor.projected,
                now_wall=time.time())
            decision, degrade = self._horizon.backstop_gate(
                decision, degrade, position_side=self.executor.projected)
            decision, degrade = self._sizing.gate(decision, degrade)   # ★ 回撤刹车 / 连亏熔断（第 7 步）
            decision, degrade = self._apply_consensus_gate(decision, consensus, degrade)  # E2 门禁
            # ★ 最小 edge 门禁（§3.7）：共识后、反思前——被拦轮此时已是 hold，而反思对 hold 本就
            # 直通 ⇒ EV 不足的轮次自动省掉一次反思 LLM 调用，门禁本身就是省钱项。
            decision, degrade = await self._apply_edge_gate(round_no, ts, ref_px,
                                                            decision, degrade)
            # 二次反思（ReAct 复核，仅交易意愿触发；只会更保守）——共识门禁后、真实执行前。
            # F2（hold-inertia-fix）：第三元 reflection_note 保真「提议了什么、为何被否决」。
            decision, degrade, reflection_note = await self._reflect_before_execute(
                round_no, ts, payload, decision, degrade)
            decision = await self._apply_sizing(round_no, ts, ref_px, decision)   # ★ 第 11 步
            executed = await self._execute_decision(decision, ref_px, i, ts)
            self._cost.note_round(bool(executed.get("traded")))          # §6.3 决策转化率
            self.rounds_completed = round_no
            # RC-2/RC-3：研判全文只走 agent_trace/_traces；进 journal 与 decision 事件的取有界摘录（≤400）。
            # 降级轮 last_analysis 恒为空串（RC-1）→ excerpt=None（journal/事件不带残段）。
            excerpt = self.decider.last_analysis[:_ANALYSIS_EXCERPT_CHARS] or None
            # R-P1-3：commit_round 收门禁后的最终 decision，journal/摘要据此记真实动作（保持上下文纯净）。
            summary = await self.decider.commit_round(payload, decision, executed,
                                                      analysis=excerpt, reflection=reflection_note)
            await self._emit_tool_call_trace(round_no, ts, decision)
            await self._emit_result_trace(round_no, ts, executed, degrade)
            reflection_verdict, reflection_reason = self._round_reflection_summary()
            await self._emit_decision(round_no, ts, decision, board, executed,
                                      market_context=payload.get("marketContext"),
                                      consensus=payload.get("consensus"),
                                      degrade_reason=degrade, analysis=excerpt,
                                      market_digest=payload.get("marketDigest"),
                                      multi_timeframe_context=payload.get("multiTimeframeContext"),
                                      wake=wake,
                                      tactical_context=payload.get("tacticalContext"),
                                      tactical_tier=payload.get("tacticalTier"),
                                      context_disclosure=payload.get("contextDisclosure"),
                                      reflection_verdict=reflection_verdict,
                                      reflection_reason=reflection_reason)
            if degrade:
                await self._emit_degraded(ts, degrade)
            if summary is not None:
                await self._emit_summary_trace(round_no, ts, summary)
                await self._emit_summary(round_no, ts, summary)
            await self._post_round(ref_px, ts)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 决策轮异常不崩会话：记 error 事件，等下一根
            logger.warning("ai-trade decision round failed: %s", exc)
            await self._emit_error_trace(round_no, ts, "本轮决策异常，已跳过")
            await self._push({"type": "error", "data": {
                "ts": ts, "code": "AI_TRADE_ROUND_FAILED", "message": "本轮决策异常，已跳过"}})
        finally:
            self._deciding = False
            self._maybe_catchup_wake()       # 轴 A：纯同步，check→claim 之间无 await

    def _maybe_catchup_wake(self) -> None:
        """上一轮结束即补跑一轮（轴 A）。纯同步：与既有两处槽位认领同范式，无竞态。

        `finally` 在 `stop()` 取消决策任务时同样会跑，故 `state != "running"` 必须显式挡掉——
        否则停止流程会在 flatten 之后凭空再起一轮真实资金决策。
        """
        if self.state != "running" or self._deciding or self._last_board is None:
            return
        now = time.time()
        ref_px = self._current_px()
        wake = self._wake.plan_catchup(self._wake_readings("interval", ref_px), now_wall=now)
        if wake is None:
            return
        self._deciding = True
        self._last_round_bar_index = self._last_bar_index
        self._last_round_px = ref_px
        self._last_decision_started_wall = now
        self._decision_task = asyncio.create_task(
            self._run_decision_round(self._last_board, self._last_bar_index,
                                     ref_px, int(now), wake))

    async def _maybe_signal_flip_wake(self) -> None:
        """轴 C：未确认根预演 → 防抖 → 投影净共识翻转即唤醒（重活在线程里，不占盯市 tick）。"""
        for alert in self._preview.take_alerts():   # 自动关轴是降级，静默降级会被当成故障
            await self._push(alert)
        if not self._preview_ready():
            return
        result = await self._preview.run(PreviewRequest(
            specs=self._specs, shadows=self._shadows, window=self._window,
            live_row=self._live_row, now_wall=time.time(), last_px_wall=self._last_px_wall))
        if result is None or self._deciding:  # 在飞轮不抢槽位，本次采样也不喂防抖器（见 plan_signal_flip）
            return
        ref_px = self._current_px()           # 以下同步认领，check→set 之间无 await
        now = time.time()
        wake = self._wake.plan_signal_flip(result, self._last_board,
                                           self._wake_readings("interval", ref_px), now_wall=now)
        if wake is None:
            return
        self._deciding = True
        self._last_round_bar_index = self._last_bar_index
        self._last_round_px = ref_px
        self._last_decision_started_wall = now
        self._decision_task = asyncio.create_task(
            self._run_decision_round(self._last_board, self._last_bar_index,
                                     ref_px, int(now), wake))

    def _preview_ready(self) -> bool:
        """预演前置条件（CLAUDE.md §六：单表达式超过三个布尔条件即抽函数）。"""
        return (self.state == "running" and not self._stale_degraded
                and self._last_board is not None and self._live_row is not None
                and self._preview.due(time.time()))

    # ---------- 决策认知轨迹（ai-auto-trading-agent-trace §3.3）----------
    async def _begin_round_trace(self, round_no: int, ts: int, ref_px: float,
                                 payload: dict, board: list[StrategySignalView]) -> None:
        """round_start：装配在飞轮次（引用入环、后续原地累积）+ 推输入快照（RV-7：须在 consensus 之后）。"""
        slim = [self._view_slim(v) for v in board]
        self._round_trace = new_round_trace(round_no, ts, ref_px, payload, slim)
        self._traces.append(self._round_trace)
        trace = self._round_trace
        await self._push(trace_event(
            "round_start", round_no, ts, lastPx=ref_px, position=trace["position"],
            signalBoard=trace["signalBoard"], marketContext=trace["marketContext"],
            multiTimeframeContext=trace["multiTimeframeContext"],
            consensus=trace["consensus"], marketDigest=trace["marketDigest"],
            trigger=trace["trigger"], profitGuard=trace["profitGuard"],
            profitGuardCooldown=trace["profitGuardCooldown"],
            signalPriors=trace["signalPriors"], wake=trace["wake"],
            tacticalContext=trace["tacticalContext"], tacticalTier=trace["tacticalTier"],
            contextDisclosure=trace["contextDisclosure"]))

    # ---------- 下单前二次反思编排（ai-trade-pre-trade-reflection §2.3；精简，装配下沉 trace.py）----------
    async def _reflect_before_execute(self, round_no: int, ts: int, payload: dict,
                                      decision: TradeDecision, degrade: str | None
                                      ) -> tuple[TradeDecision, str | None, dict | None]:
        """交易意愿决策在真实下单前经一次独立反思复核（只会更保守）；hold/撤单/关闭反思时原样直通。

        返回三元组 `(final, degrade, reflection_note)`（hold-inertia-fix F2）：第三元为
        `build_reflection_note` 注记（直通分支为 None），由 `_run_decision_round` 透传给
        `commit_round`，使 journal/摘要素材保真「曾提议开仓但被否决」。
        """
        # F-7（ai-trade-limit-orders-fix §2.8）：cancel_order 与 hold 同列直通——反思宪章是「下单前
        # 二次复核」，撤单不产生任何委托（order_plan 恒 None）、本身即风险收敛动作；拦截它反而反保守
        # （abort/fail-closed 会把撤单降级 hold，挂单敞口多活到 TTL）。勿回改为单值门控。
        if (not self.decider.reflection_enabled
                or decision.action in ("hold", "cancel_order")):
            return decision, degrade, None           # 关闭 或 不下单动作 → 无 LLM、无轨迹、无注记
        await self._begin_reflection_trace(round_no, ts, decision)
        verdict = await self.decider.reflect(
            payload, decision, emit=self._make_trace_emit(round_no, ts))
        final, new_degrade = apply_reflection(decision, degrade, verdict, self.decider.position_step)
        await self._emit_reflection_verdict_trace(round_no, ts, verdict, decision, final)
        return final, new_degrade, build_reflection_note(decision, verdict, final)

    async def _begin_reflection_trace(self, round_no: int, ts: int,
                                      decision: TradeDecision) -> None:
        """reflection_start：置在飞指示 + 推被复核决策快照（装配走 trace.py builder，RV-2）。"""
        if self._round_trace is not None:
            self._round_trace["reflectionPending"] = True
        await self._push(reflection_start_event(round_no, ts, decision))

    async def _emit_reflection_verdict_trace(self, round_no: int, ts: int, verdict,
                                             original: TradeDecision,
                                             final: TradeDecision) -> None:
        """reflection_verdict：落裁决/理由/实际收缩后仓位并清在飞指示（装配走 trace.py builder，RV-2）。"""
        adjusted = applied_shrink(original, final)
        trace = self._round_trace
        if trace is not None:
            trace["reflectionVerdict"] = verdict.verdict
            trace["reflectionReason"] = verdict.reason
            trace["reflectionAdjustedSize"] = adjusted
            trace["reflectionPending"] = False
        await self._push(reflection_verdict_event(round_no, ts, verdict, adjusted))

    def _round_reflection_summary(self) -> tuple[str | None, str | None]:
        """从在飞轮次取本轮反思裁决/理由（供 decision 事件搭顺风车；hold/关闭轮为 (None, None)）。"""
        trace = self._round_trace
        if trace is None:
            return None, None
        return trace.get("reflectionVerdict"), trace.get("reflectionReason")

    def _make_trace_emit(self, round_no: int, ts: int) -> TraceEmit:
        """返回注入 decider 的 emit 闭包：补 round/ts、原地累积 thinking/analysis/reflection、扇出 agent_trace。"""
        async def _emit(part: dict) -> None:
            phase = part.get("phase")
            data = {"phase": phase, "round": round_no, "ts": ts}
            trace = self._round_trace
            if phase == "retry":                 # §4.4：重试面包屑——重置本轮累积 + 计数后扇出
                self._apply_retry_marker(trace, part, data)
                await self._push({"type": "agent_trace", "data": data})
                return
            delta = part.get("delta")
            if delta is not None:
                data["delta"] = delta
                if trace is not None and phase in ("thinking", "analysis", "reflection"):
                    trace[phase] += delta
            name = part.get("name")
            if name is not None:
                data["name"] = name
                if trace is not None and phase == "tool_call_start":
                    trace["toolCallPending"] = True
            await self._push({"type": "agent_trace", "data": data})
        return _emit

    @staticmethod
    def _apply_retry_marker(trace: dict | None, part: dict, data: dict) -> None:
        """retry 阶段（§4.4）：把面包屑字段写入外发 `data`，并把在飞轮次累积重置——
        丢弃失败尝试的 thinking/analysis、复位 toolCallPending（R-1：清失败尝试残留的工具挂起指示）、
        state 回 deciding、累加本轮重试计数并记 lastRetryReason。"""
        data["attempt"] = part.get("attempt")
        data["maxAttempts"] = part.get("maxAttempts")
        data["reason"] = part.get("reason")
        if trace is None:
            return
        trace["thinking"] = ""
        trace["analysis"] = ""
        trace["toolCallPending"] = False
        trace["state"] = "deciding"
        trace["retries"] = int(trace.get("retries", 0)) + 1
        trace["lastRetryReason"] = part.get("reason")

    async def _emit_tool_call_trace(self, round_no: int, ts: int,
                                    decision: TradeDecision) -> None:
        """tool_call：门禁后最终决策工具入参（与真实执行一致，降级则如实呈现 hold）。"""
        tool_input = {"action": decision.action, "sizeFraction": decision.size_fraction,
                      "confidence": decision.confidence, "reason": decision.reason,
                      "orderType": decision.ord_type, "limitPrice": decision.limit_px}
        if self._round_trace is not None:
            self._round_trace["toolCall"] = tool_input
            self._round_trace["toolCallPending"] = False
        await self._push(trace_event("tool_call", round_no, ts,
                                     name=DECISION_TOOL_NAME, input=tool_input))

    async def _emit_result_trace(self, round_no: int, ts: int, executed: dict,
                                 degrade: str | None) -> None:
        """result：守护执行结果 + 降级原因；state=skipped→"skipped"，否则"done"。"""
        state = "skipped" if executed.get("skipped") else "done"
        if self._round_trace is not None:
            self._round_trace["executed"] = executed
            self._round_trace["degradeReason"] = degrade
            self._round_trace["state"] = state
        await self._push(trace_event("result", round_no, ts,
                                     executed=executed, degradeReason=degrade))

    async def _emit_summary_trace(self, round_no: int, ts: int, summary: str) -> None:
        """summary：到期上下文刷新（历史执行摘要），呼应「上下文纯净」机制（G2）。"""
        if self._round_trace is not None:
            self._round_trace["summary"] = summary
        await self._push(trace_event("summary", round_no, ts, summary=summary))

    async def _emit_error_trace(self, round_no: int, ts: int, message: str) -> None:
        """error：本轮决策异常兜底（不崩会话），state="error"。"""
        if self._round_trace is not None:
            self._round_trace["errorMessage"] = message
            self._round_trace["state"] = "error"
        await self._push(trace_event("error", round_no, ts, message=message))

    async def _apply_base_rule_gate(self, round_no: int, ts: int, payload: dict,
                                    decision: TradeDecision, degrade: str | None
                                    ) -> tuple[TradeDecision, str | None]:
        """薄封装 `base_rules.apply_ma_band_gate`（§2.5）：未装配 ⇒ 原样直通、零轨迹、零副作用。

        判定价与通道均取自本轮 payload（与喂给 prompt 的逐字同源）；铁律优先级最高，故与共识门禁并存时
        由 `degrade or reason` 的**先执行者胜出**语义报告铁律的原因。
        """
        if self.base_rule is None:
            return decision, degrade
        band = (payload.get("baseRules") or {}).get("maBand")
        decision, reason = apply_ma_band_gate(decision, band, self.base_rule,
                                              last_px=payload.get("lastPx"))
        await self._emit_base_rule_trace(round_no, ts, band, reason)
        return decision, (degrade or reason)

    async def _emit_base_rule_trace(self, round_no: int, ts: int, band: dict | None,
                                    reason: str | None) -> None:
        """base_rule 轨迹段 + 连续不可用告警升级（§2.6/§2.9）；仅规则装配时被调用。"""
        status = gate_status(band, reason)
        trace = self._round_trace
        if trace is not None:
            trace["baseRuleStatus"] = status
            trace["baseRuleZone"] = (band or {}).get("zone")
            trace["baseRulePctB"] = (band or {}).get("pctB")
            trace["baseRuleReason"] = reason
        await self._push(base_rule_event(round_no, ts, band, status, reason))
        if band is not None:
            self._band_unavailable_streak = 0
            return
        self._band_unavailable_streak += 1
        if self._band_unavailable_streak == _BAND_UNAVAILABLE_ALERT_ROUNDS:
            await self._push({"type": "error", "data": {
                "ts": ts, "code": "AI_TRADE_BASE_RULE_UNAVAILABLE",
                "message": (f"基础策略的 MA 通道已连续 {self._band_unavailable_streak} 轮无法计算"
                            f"（需至少 {self.base_rule.period + 1} 根 K 线），期间开仓一律降级观望；"
                            "平仓与撤单不受影响")}})

    async def repair_backstop(self) -> dict:
        """人工授权的单次补挂（backstop-repair §4.1）：与自动路径共用同一个 `plan_repair` 与同一条
        写路径，**只绕过 `mode`，绝不绕过 H1/H2/H3**；完成后补推一条 status（[P1-3]）。"""
        return await repair_now_from_executor(
            self._repair, self._backstop, self.executor, self._session_readings(time.time()),
            on_change=lambda: self._push({"type": "status", "data": self._status_payload()}))

    def _horizon_facts(self, now_wall: float) -> HorizonFacts:
        """本轮持仓周期断面（成本按**持仓方向**估，[P1-2]：按动作推方向会让平多仓恒记 0 资金费）。"""
        return build_horizon_facts(
            self.executor, self._session_readings(now_wall), stale_feed=self._stale_degraded,
            cost_bps=self._cost.estimate_cost_for_position(self.executor.projected,
                                                           BAR_SECONDS[self.bar]))

    async def _apply_horizon_gate(self, round_no: int, ts: int, decision: TradeDecision,
                                  degrade: str | None) -> tuple[TradeDecision, str | None]:
        """薄封装 `HorizonRuntime.gate`（§2.5）；未装配 ⇒ 原样直通、零轨迹。

        [P1-7] 断面在此**重新采集**（payload 那一份隔着一次完整 LLM 调用），方向安全：两次之间只有
        时间向前流逝。轨迹取 `last_reason` 而非合成 `degrade`——后者先手胜出会把本层的码吞掉。
        """
        if self.horizon_rule is None:
            return decision, degrade
        decision, degrade, snap = self._horizon.gate(decision, degrade,
                                                     self._horizon_facts(time.time()))
        self._round_horizon = snap
        reason = self._horizon.last_reason
        status = horizon_gate_status(snap, reason)
        apply_horizon_trace(self._round_trace, snap, status, reason)
        await self._push(horizon_event(round_no, ts, snap, status, reason))
        if self._horizon.released_streak == _HORIZON_RELEASED_ALERT_ROUNDS:
            await self._push(horizon_released_event(ts, self._horizon.released_streak,
                                                    (snap or {}).get("releaseReason")))
        return decision, degrade

    async def _apply_edge_gate(self, round_no: int, ts: int, ref_px: float,
                               decision: TradeDecision, degrade: str | None
                               ) -> tuple[TradeDecision, str | None]:
        """薄封装 `CostRuntime.evaluate`（§3.7）：`k_min<=0` ⇒ 原样直通、零事件、零副作用。"""
        out = self._cost.evaluate(
            decision, last_px=ref_px, atr_px=self._atr_px,
            take_profit_pct=self.executor.risk.take_profit_pct,
            bar_seconds=BAR_SECONDS[self.bar],
            prior_ev_bps=self._prior.last_edge_bps(decision.action),
            prior_binding=bool(self.executor.settings.ai_trade_edge_prior_enabled))
        if out is None:                       # 门禁空转：只按节流搭一次会话内成本快照（§5.4）
            await self._emit_cost_snapshot(round_no)
            return decision, degrade
        await self._emit_edge_gate_trace(round_no, ts, out)
        return out.decision, (degrade or out.reason)

    async def _emit_edge_gate_trace(self, round_no: int, ts: int, out) -> None:
        """edge_gate 轨迹段 + 连续不可用告警升级（§3.7/§5.3；装配与计数均已下沉）。"""
        streak = self._cost.note_gate(out)
        apply_edge_gate_trace(self._round_trace, out.wire, out.reason)
        await self._push(edge_gate_event(round_no, ts, out.wire, self._cost.snapshot()))
        if streak == _EDGE_UNAVAILABLE_ALERT_ROUNDS:
            await self._push(edge_gate_unavailable_event(ts, streak))

    async def _emit_cost_snapshot(self, round_no: int) -> None:
        """§5.4 L0/L1 通道：门禁空转时每 N 轮把只读成本快照搭在既有 status 事件上（纯加法键）。"""
        snap = self._cost.snapshot_if_due(round_no)
        if snap is not None:
            await self._push({"type": "status",
                              "data": {**self._status_payload(), "costSnapshot": snap}})

    async def _apply_sizing(self, round_no: int, ts: int, ref_px: float,
                            decision: TradeDecision) -> TradeDecision:
        """★ 第 11 步：仓位求解 + ATR 止损覆盖（只缩不放、绝不改 action）；未装配 ⇒ 原对象直通零事件。"""
        decision, _, wire = self._sizing.resolve_live(
            decision, self.executor, ref_px=ref_px, atr_px=self._atr_px,
            step=self.decider.position_step, prior_view=self._prior.coverage_wire(),
            cost_bps=self._cost.estimate_cost_only(decision, BAR_SECONDS[self.bar]))
        if wire:
            apply_sizing_trace(self._round_trace, wire)
            await self._push(sizing_event(round_no, ts, wire))
        return decision

    def _apply_consensus_gate(self, decision: TradeDecision, consensus: Consensus,
                              degrade: str | None) -> tuple[TradeDecision, str | None]:
        """薄封装 `signals.apply_consensus_gate`（§2.8/R1）：传 `settings.ai_trade_min_consensus`。

        对外行为逐字不变——门禁降级则 `degradeReason` 归并为 `degrade or gate_reason`（保留决策器
        先前的回落原因，如 timeout），否则透传原 `degrade`。共识门禁纯核心已上移至 `signals` 供回测共用。
        """
        decision, gate_reason = apply_consensus_gate(
            decision, consensus, self.executor.settings.ai_trade_min_consensus)
        return decision, (degrade or gate_reason)

    async def _execute_decision(self, decision: TradeDecision, ref_px: float, i: int,
                                ts: int) -> dict:
        """映射决策为 Signal 并经 LiveExecutor 守护执行（一切写经 _exec_lock 串行，P1-1）。

        `cancel_order` 在**进入信号翻译之前**先行分发（ai-trade-limit-orders §2.2a 第一层防线）：
        它根本不调用 `decision_to_signals`。第二层是 `decision_to_signals` 自身的白名单（M-12）——
        任一层单独成立即可阻断「Agent 说撤单 → 被静默翻译成市价平掉真实持仓」。
        """
        if decision.action == "cancel_order":
            return await self._execute_cancel(decision, ts)
        signals = self._decision_to_signals(decision, board_digest=self._board_digest())
        if not signals:
            return {"ok": True, "action": decision.action, "skipped": False,
                    "side": self.executor.projected, "qty": self.executor.entry_qty_sz,
                    "positionAfter": self.executor.projected}
        order_plan = decision_to_order_plan(decision)
        async with self._exec_lock:
            if self.state != "running":       # stop() 抢先置 stopped：本轮不再下单（避免停止竞态下多此一举的开仓+随即被 flatten 平掉，白付手续费）
                return {"ok": True, "action": decision.action, "skipped": True,
                        "reason": "stopped", "side": self.executor.projected,
                        "qty": self.executor.entry_qty_sz,
                        "positionAfter": self.executor.projected}
            events = await self.executor.apply(signals, ref_px=ref_px, bar_index=i, ts=ts,
                                               order_plan=order_plan)
            events += await self.executor.reconcile(ref_px, ts)
        await self._dispatch(events)
        await self._push_status_if_side_changed()
        traded = any(e.get("type") == "trade" for e in events)
        return {"ok": True, "action": decision.action, "skipped": False, "traded": traded,
                "side": self.executor.projected, "qty": self.executor.entry_qty_sz,
                "positionAfter": self.executor.projected}

    async def _execute_cancel(self, decision: TradeDecision, ts: int) -> dict:
        """`cancel_order` 前置分发（§2.2a 第一层）：只撤挂单，**绝不触碰任何持仓**。

        无挂单时为良性 no-op（回 status 事件、不报错、不改 `projected`）。
        """
        async with self._exec_lock:
            if self.state != "running":
                return {"ok": True, "action": decision.action, "skipped": True,
                        "reason": "stopped", "side": self.executor.projected,
                        "qty": self.executor.entry_qty_sz,
                        "positionAfter": self.executor.projected}
            events = await self.executor.cancel_resting("agent", ts)
        await self._dispatch(events)
        return {"ok": True, "action": decision.action, "skipped": False, "traded": False,
                "side": self.executor.projected, "qty": self.executor.entry_qty_sz,
                "positionAfter": self.executor.projected}

    def _decision_to_signals(self, decision: TradeDecision, *, board_digest: str) -> list[Signal]:
        """决策 → Signal：调纯核心 `signals.decision_to_signals` + 补 AI explain 富化（§2.8/R1）。

        对外行为逐字不变——实盘的 `board_digest`/atomic-explain 富化保留（回测不需该富化，直接用纯核心）。
        `open_long/short` 带 sizeFraction，`close` 带理由，均补 `explain`；`hold=[]`。
        """
        signals = decision_to_signals(decision)
        if not signals:
            return signals
        explain = self._ai_explain(decision.action, decision.reason, decision.confidence,
                                   board_digest)
        for sig in signals:
            sig.explain = explain
        return signals

    @staticmethod
    def _ai_explain(action: str, reason: str, confidence: float | None,
                    digest: str) -> dict:
        """AI 决策归因（透传到 trade.explain，面板展示「AI 决策」来源 + 置信度 + 信号板摘要）。

        M-6：此处原为**裸下标** `{...}[action]`（仅 3 键、连 `hold` 都没有），任何新动作当轮即
        KeyError。改 `.get()` 并显式回落——本函数只在 `_decision_to_signals` 产出了非空信号后才被
        调用（即 action ∈ {open_long, open_short, close}），故回落分支实为不可达的防御，但**绝不**
        再让一个新动作把整轮决策炸掉。
        """
        side = _ACTION_SIDE.get(action, "close")
        headline = f"AI 决策 · {_ACTION_LABEL.get(action, action)}"
        explain = build_atomic_explain(side, reason or headline,
                                       summary="AI 综合多策略信号板研判后的自动交易决策")
        explain["meta"] = {"source": "ai", "confidence": confidence, "signalBoardDigest": digest}
        return explain

    def _board_digest(self) -> str:
        """把当前影子立场压成一行短摘要（随 explain 落库，供归因展示）。"""
        return ", ".join(f"{s.pos.side}" for s in self._shadows)

    async def _post_round(self, ref_px: float, ts: int) -> None:
        await self._emit_equity(ref_px, ts)
        await self._emit_position(ref_px)
        # 保护先于会话止盈（§2.5）：刚开仓的轮次被 min_hold_s 天然短路，故此处挂点无额外成本。
        await self._maybe_profit_exit(ts)
        await self._maybe_session_exit(ref_px, ts)

    async def _maybe_session_exit(self, ref_px: float, ts: int) -> None:
        """会话级金额阈值：累计盈亏 ≥ +TP 或 ≤ −SL → 自动平仓（经 _exec_lock）+ 停会话。"""
        pnl = self.executor.session_pnl_usd
        reason = None
        if self.take_profit_usd is not None and pnl >= self.take_profit_usd:
            reason = "session_take_profit"
        elif self.stop_loss_usd is not None and pnl <= -self.stop_loss_usd:
            reason = "session_stop_loss"
        if reason is not None:
            await self.stop(reason)

    # ---------- 利润保护层（ai-trade-profit-protection §2.3~§2.5）----------
    def _position_payload(self, mark_px: float) -> dict | None:
        """`executor.position_payload` 的富化包装：持仓 payload 搭车一份利润保护快照。

        **保护段刻意忽略 `mark_px` 形参**（§2.2 单源硬约束 / P1-2）：调用方传的是 `_latest_mark_px()`
        / `_ref_px()`——**最近确认根收盘价**，15m 周期上最坏滞后一整根。判定价与展示价必须同源，否则
        UI 上的退出线会与保护层真正据以平仓的数字不是同一个，正是 `base_rules` 的 [R-5] 教训复发。
        接受该形参只是为了不改既有调用点签名。
        """
        pos = self.executor.position_payload(mark_px)
        if pos is not None and self.profit_rule is not None:
            snap = self._refresh_profit_state(time.time())
            pos = pos if snap is None else {**pos, "profitGuard": snap}
        return self._cost.decorate_position(pos, bar_seconds=BAR_SECONDS[self.bar])

    def _session_readings(self, now_wall: float) -> SessionReadings:
        """把会话侧的六个时钟 / 断面读数打包（F1：ATR 只进上下界计算，绝不参与触发判定，G-5）。"""
        return SessionReadings(
            seq=self._position_seq, now_wall=now_wall, mark_px=self._current_px(),
            bar_index=self._last_bar_index,
            px_age_s=(max(0.0, now_wall - self._last_px_wall) if self._last_px_wall > 0 else 0.0),
            atr_px=self._atr_px)

    def _refresh_profit_state(self, now_wall: float) -> dict | None:
        """组装本轮持仓断面并交给 `ProfitGuardRuntime.refresh`；未装配或无持仓 → None。

        F0 之后本方法只剩「有没有仓 → 组装 facts → 委托运行时」三步；epoch 身份判定、峰值推进与
        fail-open 前置校验全部在纯核心里（`advance_state` / `unavailable_reason_for`）。

        本方法是保护快照 status 的**唯一**产出点，故 `WakeRuntime.note_guard_status` 挂在这里：唤醒
        分类要读的「本 tick 保护状态」与保护层真正据以平仓的是同一个数，调用方不得另取一次。
        """
        if self.profit_rule is None:
            self._wake.note_guard_status(None)
            return None
        if self.executor.projected == "flat":
            self._guard.clear_state()
            self._wake.note_guard_status(None)
            return None
        facts = build_position_facts(self.executor, self._session_readings(now_wall))
        snap = self._guard.refresh(facts, stale_feed=self._stale_degraded)
        self._wake.note_guard_status((snap or {}).get("status"))
        return snap

    async def _maybe_profit_exit(self, ts: int) -> None:
        """确定性利润保护：峰值回吐 / 保本 / 硬止盈触发即市价平仓（§2.5）。

        本层只会产生 `close`，**永远不会**阻止或改写模型的任何决策；与共识门禁、MA 通道铁律、反思层
        互不干涉（后三者一律不拦 `close`，故不存在「保护要平、门禁不让」的死锁）。判定只用实时价，
        不等 K 线确认（诊断根因 ④）。
        """
        if self.profit_rule is None or self.state != "running":
            return
        snap = self._refresh_profit_state(time.time())
        if snap is None or snap.get("status") == STATUS_UNAVAILABLE:
            return                                   # 含 §2.4 第 0/0b 条 fail-open 豁免
        verdict = evaluate_profit_guard(snap, self.profit_rule)
        if verdict is None:
            return
        px = self._current_px()
        events = await self._apply_profit_exit(verdict, snap, px, ts)
        if events is None:
            return                                   # 取锁期间局面已变（复检失败），静默返回
        await self._dispatch(events)                 # ← _position_seq 随 close 成交在此自增（§2.3.1）
        await self._push_status_if_side_changed()
        await self._report_profit_exit(verdict, events, ts)

    async def _apply_profit_exit(self, verdict, snap: dict, px: float,
                                 ts: int) -> list[dict] | None:
        """经 `_exec_lock` 走 executor 的**唯一**平仓路径下市价单；取锁后局面已变 → None。

        绝不新写平仓路径：`_settle_close` 封装了「先算 pnl/pnlPct 再复位 entry」这个顺序敏感约束。
        挂单冲突由 `_do_close → _prepare_resting_slot` 既有逻辑处理（先撤后平）。
        """
        signal = Signal(side="close", reason=verdict.reason_code)
        signal.explain = build_profit_guard_explain(
            pos_side=snap.get("side") or "", trigger_label=verdict.label,
            entry_price=snap.get("entryPx") or 0.0, exit_price=px,
            peak_pnl=snap.get("peakUnrealizedPnlUsd"),
            giveback_ratio=snap.get("givebackRatio"), pnl=snap.get("unrealizedPnlUsd"),
            # 只传**规则量**断面：捕获率是成交后量，此刻 trade.pnl 尚不存在（P1-5）。
            trail={"givebackPctEffective": snap.get("givebackPctEffective"),
                   "trailBasis": snap.get("trailBasis")})
        async with self._exec_lock:
            if self.state != "running" or self.executor.projected == "flat":
                return None       # LLM 决策轮可能在等锁期间已平仓——目的已达成，复检非防御性噪音
            events = await self.executor.apply([signal], ref_px=px,
                                               bar_index=self._last_bar_index, ts=ts)
            events += await self.executor.reconcile(px, ts)
        return events

    async def _report_profit_exit(self, verdict, events: list[dict], ts: int) -> None:
        """[P1-1] 以 `close` 成交事件为**唯一**成交判据，绝不据「已调用 apply」宣称已平仓。

        `_do_close` 有四条**不平仓**的返回路径（撤单回 unknown / 撤单撞成交 / `close_position` 抛
        `OKXError` / `projected` 已 flat）。假报一次不存在的平仓，会让用户以为利润已锁定而实际仓位
        还在裸奔——失败即保留状态、下一次盯市自然重试是正确行为，只是不许对外宣称成功。
        """
        closed = any(e.get("type") == "trade" and (e.get("data") or {}).get("side") == "close"
                     for e in events)
        if closed:
            self._guard.exits += 1
            self._guard.hold_streak = 0       # 该仓已不存在，连续盈利持有链条终结（§2.6 c）
            self._guard.clear_state()
            self._guard.pending_protect_note = {**verdict.note, "ts": int(ts)}
            # F2（§2.2.1）：**只有保护层自己确实平掉了一笔**才开启冷却（Agent 主动 close / 会话级
            # 止盈止损 / 外部平仓一律不开启——冷却是补偿，不是对模型正常交易的惩罚）。
            self._guard.start_cooldown(side=str(verdict.note.get("side") or ""),
                                       reason_code=verdict.reason_code, label=verdict.label,
                                       now_wall=time.time())
            await self._push({"type": "status", "data": {
                **self._status_payload(),
                # §六.1b / P0-1：措辞一律由 pnl **符号**派生。硬编码「锁定利润」在保本线触发、硬止盈
                # 滑点不利、或 ATR 下界把线压到入场价附近时都会说谎。
                "message": (f"利润保护触发（{verdict.label}）：已市价平仓，"
                            f"{describe_exit_outcome(verdict.note.get('pnlUsd'))}")}})
            return
        if self._guard.warned_seq == self._position_seq:
            return                            # P2-8：每个持仓周期至多推一条 warn，避免 5s 刷屏
        self._guard.warned_seq = self._position_seq
        await self._push({"type": "status", "data": {
            **self._status_payload(),
            "message": (f"利润保护已触发（{verdict.label}）但本次平仓未成交，"
                        "将在下一次盯市重试（交易所侧止盈止损仍生效）")}})

    def _update_profit_hold_streak(self, stage1_action: str, stage1_degrade: str | None,
                                   snapshot: dict | None) -> None:
        """连续「浮盈状态下继续持有」计数（F0：判定下沉运行时，调用点与语义零变化）。"""
        self._guard.update_hold_streak(stage1_action, stage1_degrade, snapshot)

    def _record_profit_commitment(self, stage1_action: str, hold_exit_px: float | None,
                                  snapshot: dict | None) -> None:
        """把模型本轮承诺的退出线记进运行时棘轮（C1 §2.2.4；仅 hold + 有值时）；未装配/未开启 ⇒ 内部短路。"""
        if stage1_action != "hold" or hold_exit_px is None:
            return
        self._guard.record_commitment(hold_exit_px, snapshot)

    def _inject_profit_guard(self, payload: dict) -> None:
        """注入保护快照 / 一次性回执 / 冷却视图（F0 下沉运行时）；未装配 ⇒ 整键缺席 ⇒ prompt 逐字回退。"""
        now_wall = time.time()
        self._guard.inject(
            payload, self._refresh_profit_state(now_wall),
            note_after_rounds=self.executor.settings.ai_trade_profit_guard_note_after_rounds,
            now_wall=now_wall)

    def _apply_profit_cooldown_gate(self, decision: TradeDecision,
                                    degrade: str | None) -> tuple[TradeDecision, str | None]:
        """★ 利润保护冷却门禁（F2 §2.2.2）：铁律**之后**、共识**之前**，`degrade or reason` 先手胜出。

        排在共识之前，是因为冷却是**本系统刚刚亲手做过的动作**的直接后果，解释力强于共识票数；
        三个豁免动作由 `apply_profit_cooldown` 纯核心保证，此处不复判。
        """
        decision, reason = apply_profit_cooldown(decision, self._guard.cooldown,
                                                 now_wall=time.time())
        return decision, degrade or reason

    async def _expire_profit_cooldown(self) -> None:
        """两条冷却到期时各补推一次 `status`（§4.2 / turnover-budget [P1-3]）——不推则前端 chip 会
        一直挂着（后端已不再拦，UI 却说在拦）。**两者都必须求值**：`or` 短路会吞掉另一层的翻转。"""
        guard_expired = self._guard.expire_cooldown(time.time())
        if self._horizon.expire_turnover_cooldown(time.time()) or guard_expired:
            await self._push({"type": "status", "data": self._status_payload()})

    # ---------- decision / summary 事件 ----------
    def _decision_as_of_ts(self, ts: int, wake: dict) -> int:
        """本轮决策瞬刻（epoch 秒，**墙钟口径**）：K 线轮 = 该根收盘时刻；定时轮 = `ts` 本身。

        会话在两条唤醒路径上给出的 `ts` 语义不同（K 线轮是根**开盘** ts、定时轮是墙钟 `int(now)`），
        故「决策瞬刻」只能有一个真源、按 trigger 分支求一次，再同时喂战术段与 `changeSinceLastRound`。
        若沿用既有 MTF 的 `ts + BAR_SECONDS[bar]`，定时轮的 `as_of_ts` 会变成「现在 + 一整根」这个
        **未来时刻**：`ageSeconds` 凭空多出一整根，而战术周期的 stale 阈值只有两根（1m 仅 120 秒）
        ⇒ 每个定时轮的每个战术周期恒被标成「数据陈旧」，而系统 prompt 决策纪律 #2 明写「陈旧或缺失
        周期不得补猜」——等于把刚取回来的证据亲手命令模型忽略。反过来传裸 `ts`，K 线轮又会把整根
        执行周期内的战术根全部裁掉。
        """
        if wake.get("trigger") == "candle":
            return ts + BAR_SECONDS[self.bar]
        return ts

    def _build_decision_payload(self, round_no: int, ts: int, ref_px: float,
                                board: list[StrategySignalView],
                                wake: dict) -> dict:
        payload = {
            "round": round_no, "barTs": ts, "lastPx": ref_px,
            "trigger": wake.get("trigger", "candle"),
            "position": self.executor.position_payload(ref_px),
            # §4.2：当前挂单只读上下文（无挂单 → None，prompt 整段省略）。Agent 看不见自己的挂单
            # 就无法就它做任何决策——这是 `cancel_order` 可用的前提。
            "restingOrder": resting_payload(self.executor.resting, int(time.time())),
            "sessionPnlUsd": round(self.executor.session_pnl_usd, 4),
            "budgetUsd": self.executor.budget_usd,
            "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
            "marketContext": build_market_context(         # E1：只读；数据不足/禁用/异常 → None
                self._window.base_df(), self.bar, self.executor.settings),
            "marketDigest": build_market_digest(           # R1：价格走势/成交量/因子证据；同样安全降级 → None
                self._window.base_df(), self.bar, self.executor.settings),
            "multiTimeframeContext": self._build_multi_timeframe_context(ts, ref_px),
            "signalBoard": [self._view_full(v) for v in board]}
        # N3（hold-inertia-observability）：连续观望达到阈值才注入（0=关闭）。payload 在 decide 之前
        # 装配，故值 = 截至上一轮的连续观望数。阈值未达/关闭/回测（backtest.py 自建 payload）→ 键缺失
        # → prompt 逐字节不变（F4a 冻结范式）。注记文本会经 commit_round 重放进 _context 该轮 user
        # 消息（R2-04 有意行为：模型上下文如实保留它当轮看到的输入，随窗口裁剪/摘要自然消退）。
        threshold = self.executor.settings.ai_trade_hold_streak_note_after
        if threshold > 0 and self.hold_streak >= threshold:
            payload["holdStreak"] = self.hold_streak
        # 【基础策略】通道快照（§2.2/§4.2）：规则未装配 ⇒ **整键缺席**（不是 None）⇒ prompt 渲染与
        # 回测 payload 逐字节回退。band 每轮现算现用，与 lastPx / marketDigest 同一份因果窗口。
        if self.base_rule is not None:
            payload["baseRules"] = {
                "maBand": build_ma_band(self._window.base_df(), self.base_rule)}
        self._inject_profit_guard(payload)      # §2.6 b/d：未装配 ⇒ 整键缺席 ⇒ prompt 逐字回退
        # 持仓周期段（§2.4 双快照契约）：这是 **round-start** 那一份（带 asOfWall），只喂 prompt；判定
        # 另在 gate 里重采。须在 `_inject_profit_guard` 之后——它顺带给保护快照打 `horizonArmed` 标记。
        self._horizon.inject(payload, self._horizon.refresh(self._horizon_facts(time.time())))
        self._sizing.inject(payload)            # 同款「整键缺席」范式（vol-sizing §3.8）
        priors = self._prior.build(board, payload.get("marketContext"),
                                   self._window.base_df())
        if priors is not None:                  # 同款「整键缺席」范式（§3.9 的 G-1 逐字节断言）
            payload["signalPriors"] = priors
        self._cost.set_prior_view(self._prior.coverage_wire())   # P1-3：两条快照通道同源
        self._cost.inject(payload, last_px=ref_px, atr_px=self._atr_px,
                          bar_seconds=BAR_SECONDS[self.bar])     # 轴 D：总闸关 ⇒ 整键缺席
        # 唤醒原因 + 战术周期证据 + 两个披露档（wake-aware-context §2.6）：三总闸各管自己的键，
        # 关闭即**整键缺席**（不是 None）⇒ prompt 渲染与既有 payload 冻结断言逐字节回退。
        self._wake.inject(payload, InjectionContext(
            wake=wake, frames=self._window.tactical_frames(),
            as_of_ts=self._decision_as_of_ts(ts, wake), last_px=ref_px,
            position_side=self.executor.projected,
            execution_primary=(payload.get("marketContext") or {}).get("primary")))
        return payload

    def _build_multi_timeframe_context(self, ts: int, ref_px: float) -> dict | None:
        try:
            return build_multi_timeframe_context(
                execution_bar=self.bar,
                frames=self._window.context_frames(),
                as_of_ts=ts + BAR_SECONDS[self.bar],
                last_px=ref_px,
                settings=self.executor.settings,
            )
        except ValueError as exc:
            logger.warning(
                "invalid multi-timeframe context contract: bar=%s ts=%s price=%s (%s)",
                self.bar, ts, ref_px, exc,
            )
            return None

    @staticmethod
    def _consensus_wire(consensus: Consensus) -> dict:
        """`Consensus` dataclass → camelCase wire dict（供 prompt 与 decision 事件，E2）。"""
        return {"longVotes": consensus.long_votes, "shortVotes": consensus.short_votes,
                "flatVotes": consensus.flat_votes, "total": consensus.total,
                "netStance": consensus.net_stance, "agreement": consensus.agreement}

    @staticmethod
    def _view_full(v: StrategySignalView) -> dict:
        risk = v.risk_params
        return {"strategyId": v.strategy_id, "name": v.name, "stance": v.stance,
                "latestSignal": v.latest_signal, "strength": round(v.strength, 4),
                "reason": v.reason, "stopLossPct": risk.stop_loss_pct,
                "takeProfitPct": risk.take_profit_pct, "maxHoldBars": risk.max_hold_bars}

    @staticmethod
    def _view_slim(v: StrategySignalView) -> dict:
        return {"strategyId": v.strategy_id, "name": v.name, "stance": v.stance,
                "latestSignal": v.latest_signal, "strength": round(v.strength, 4),
                "reason": v.reason}

    async def _emit_decision(self, round_no: int, ts: int, decision: TradeDecision,
                             board: list[StrategySignalView], executed: dict, *,
                             market_context: dict | None = None,
                             consensus: dict | None = None,
                             degrade_reason: str | None = None,
                             analysis: str | None = None,
                             market_digest: dict | None = None,
                             multi_timeframe_context: dict | None = None,
                             wake: dict | None = None,
                             tactical_context: dict | None = None,
                             tactical_tier: str | None = None,
                             context_disclosure: str | None = None,
                             reflection_verdict: str | None = None,
                             reflection_reason: str | None = None) -> None:
        """推 decision 事件；E1/E2/E3 + R1 研判摘录/证据摘要搭顺风车下发（旧前端忽略未知字段，向前兼容）。

        `analysis` 为有界摘录（≤`_ANALYSIS_EXCERPT_CHARS`，供左栏决策卡首段；完整思维链见 agent_trace/
        snapshot.agentTraces，RC-3）；`market_digest` 为本轮价格走势/成交量/因子证据（小、~10 数值）；
        `wake` 为本轮唤醒断面（`wake["trigger"]` 即既有 `data.trigger` 的取值，wire 语义不变；唤醒原因
        总闸关闭时 `data.wake` 下发 None，前端整段回退旧文案）；`reflection_verdict/reason` 为下单前
        二次反思裁决（无反思轮为 None）。
        """
        data = {"ts": ts, "round": round_no, "action": decision.action,
                "sizeFraction": decision.size_fraction, "confidence": decision.confidence,
                "orderType": decision.ord_type, "limitPrice": decision.limit_px,
                # §4.2：本轮结束时的挂单断面（无挂单 → None，前端整行省略）。与喂给 Agent 的
                # prompt 同一个 `resting_payload` 真源，故人看到的与 Agent 看到的逐字一致。
                "restingOrder": resting_payload(self.executor.resting, int(time.time())),
                "reason": decision.reason, "signalBoard": [self._view_slim(v) for v in board],
                "executed": executed, "marketContext": market_context,
                "consensus": consensus, "degradeReason": degrade_reason,
                "analysis": analysis, "marketDigest": market_digest,
                "multiTimeframeContext": multi_timeframe_context,
                "trigger": (wake or {}).get("trigger", "candle"),
                "wake": wake if self._wake.reason_enabled else None,
                "tacticalContext": tactical_context, "tacticalTier": tactical_tier,
                "contextDisclosure": context_disclosure,
                "reflectionVerdict": reflection_verdict, "reflectionReason": reflection_reason,
                # N5（hold-inertia-observability）：decide 后已更新，语义=「截至本轮」的连续真实观望；
                # 每轮随 decision 事件下发，是前端 live 值的最新真源（跳过轮事件不带此键）。
                "holdStreak": self.hold_streak,
                # 本轮**判定用**的持仓周期快照（G-4：trace / decision 与门禁同源同值）；未装配 ⇒ None。
                "horizon": self._round_horizon}
        self._last_decision = data
        self._decisions.append(data)
        await self._push({"type": "decision", "data": data})

    async def _emit_decision_skipped(self, ts: int, board: list[StrategySignalView]) -> None:
        data = {"ts": ts, "round": self.rounds_completed, "action": "hold",
                "sizeFraction": None, "confidence": None, "reason": "上一轮决策仍在进行，本轮跳过",
                "signalBoard": [self._view_slim(v) for v in board],
                "executed": {"ok": True, "skipped": True, "reason": "decision_in_flight"},
                "marketContext": None, "consensus": None, "degradeReason": None,
                "analysis": None, "marketDigest": None,
                "multiTimeframeContext": None, "trigger": "candle",
                # §2.7：被 in-flight 守卫丢弃的 K 线轮从此**自解释**——用户能看到「这根 K 线的首次
                # 研判被推迟了」，且下一次定时唤醒必被判为 bar_advanced（模型会被明确告知）。
                "wake": ({"kind": "candle_close", "trigger": "candle", "dropped": True,
                          "backlog": self._wake.backlog_wire()}
                         if self._wake.reason_enabled else None),
                "tacticalContext": None, "tacticalTier": None, "contextDisclosure": None,
                "reflectionVerdict": None, "reflectionReason": None}
        self._last_decision = data
        self._decisions.append(data)
        await self._push({"type": "decision", "data": data})

    async def _emit_degraded(self, ts: int, reason: str) -> None:
        self.degraded = True
        await self._push({"type": "status", "data": {
            **self._status_payload(),
            "message": f"AI 决策降级为观望（{reason}）：本轮未下单"}})

    async def _emit_summary(self, round_no: int, ts: int, summary: str) -> None:
        await self._push({"type": "summary", "data": {"ts": ts, "round": round_no,
                                                      "summary": summary}})

    # ---------- 净值 / 持仓 / 状态 ----------
    async def _emit_equity(self, ref_px: float, ts: int) -> None:
        equity = self.executor.budget_usd + self.executor.session_pnl_usd
        self._peak_equity = max(self._peak_equity, equity)
        dd = (equity / self._peak_equity - 1.0) if self._peak_equity > 0 else 0.0
        self._sizing.note_executor(self.executor, ref_px)   # 刹车权益推进 + 敞口簿轮末兜底（S-14）
        record = {"ts": ts, "equity": equity, "drawdown": min(dd, 0.0)}
        self._equity.append(record)
        await self._push({"type": "equity", "data": record})

    async def _emit_position(self, ref_px: float) -> None:
        pos = self._position_payload(ref_px)     # 富化包装：持仓 payload 搭车利润保护快照（§3.1）
        if pos is not None:
            await self._push({"type": "position", "data": pos})

    async def _push_status_if_side_changed(self) -> None:
        side = self.executor.projected
        if side == self._last_status_side:
            return
        self._last_status_side = side
        await self._push({"type": "status", "data": self._status_payload()})

    def _status_payload(self) -> dict:
        return {"state": self.state, "warmedBars": self._warmed_bars,
                "sessionPnlUsd": self.executor.session_pnl_usd,
                "positionSide": self.executor.projected,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "roundsCompleted": self.rounds_completed,
                "roundsSinceSummary": self.decider.rounds_since_summary,
                "contextRefreshRounds": self.context_refresh_rounds,
                "wakeIntervalMinutes": self._wake_interval_minutes(),
                "wakeSkips": self.wake_skips,
                **self._wake.status_counts(self._preview.stats()),   # 调度四计数（A-6 同源）
                "holdStreak": self.hold_streak,
                # F2 冷却三载体之一（§4.2）：**不挂 position**——冷却期内按定义已无持仓。
                "profitGuardCooldown": build_cooldown_view(self._guard.cooldown,
                                                           now_wall=time.time()),
                # 持仓周期快照（§4.4）：供面板 chip 在无决策事件时也能更新倒计时；未装配 ⇒ None。
                "horizon": self._horizon.last_snapshot,
                # 验收闭环四键（horizon-acceptance [P0-3] 主通路）：成员级实时刷新走这条既有 WS
                # status；分组合并口径另走 15s REST 轮询。未装配 / 零样本 ⇒ 各键为 None 而非缺席。
                **self.acceptance_bundle(),
                "stopReason": self.stop_reason,
                "degraded": self.executor.degraded or self._stale_degraded or self.degraded,
                "flattenFailed": self.flatten_failed, "residual": self.residual}

    def acceptance_bundle(self) -> dict:
        """验收闭环四键（status / 落库 / 分组合并共用**同一次**装配，绝不两处各算一份）。"""
        return build_bundle(self._horizon, self._cost,
                            settings=self.executor.settings,
                            wall_seconds=time.time() - self.started_ts)

    def _wake_interval_minutes(self) -> float | None:
        """墙钟定时唤醒周期回执换算（秒→分，关闭态存 None，与分组/config 口径统一，RN-7）。"""
        return round(self.wake_interval_s / 60, 4) if self.wake_interval_s > 0 else None

    def snapshot(self) -> dict:
        mark = self._ref_px()
        # RV-1：在飞轮 _round_trace 是被原地 mutate 的引用；对每轮做浅拷贝（顶层键即冻结，
        # thinking/analysis 为不可变 str 且 mutate 走键重绑定，不回灌拷贝出的 dict），避免快照握手
        # await 期间决策任务续写导致重连客户端对同段文字二次追加（每段增量恰好下发一次）。
        return {"trades": list(self.executor.trades),
                "equity": list(self._equity),
                "position": self._position_payload(mark),
                "status": self._status_payload(),
                "decisions": list(self._decisions),
                "contextSummary": self.decider.summary,
                "agentTraces": [dict(t) for t in self._traces]}

    def info(self) -> dict:
        return {"sessionId": self.session_id, "runId": self.run_id, "state": self.state,
                "instId": self.inst_id, "bar": self.bar,
                "strategyIds": self.strategy_ids, "strategyNames": self.strategy_names,
                "budgetUsd": self.executor.budget_usd, "leverage": self.executor.leverage,
                "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
                "contextRefreshRounds": self.context_refresh_rounds,
                "wakeIntervalMinutes": self._wake_interval_minutes(),
                "roundsCompleted": self.rounds_completed,
                "roundsSinceSummary": self.decider.rounds_since_summary,
                "positionSide": self.executor.projected,
                "sessionPnlUsd": self.executor.session_pnl_usd, "startedTs": self.started_ts,
                "lastDecision": self._last_decision, "contextSummary": self.decider.summary,
                "degraded": self.executor.degraded or self._stale_degraded or self.degraded,
                "flattenFailed": self.flatten_failed}

    # ---------- 持久化 ----------
    async def _finalize_run(self, end_ts: int) -> None:
        budget = self.executor.budget_usd
        realized = self.executor.realized_pnl_usd
        metrics = {**_empty_metrics(),
                   "totalReturn": (realized / budget) if budget > 0 else 0.0,
                   "tradeCount": len(self.executor.trades),
                   "avgPnl": realized / max(1, len(self.executor.trades)),
                   "roundsCompleted": self.rounds_completed,
                   "finalSummary": self.decider.summary,
                   "flattenFailed": self.flatten_failed,
                   "degradedSeen": self._degraded_seen}
        # ai-trade-stats-dashboard §3.3 / P1-1 / R-4：加法富化 metrics（供历史详情画权益曲线 + AI 决策概况）。
        # 三段 digest 构造包在就地 try/except——调用点 stop() 的 flatten try 不覆盖本函数，故富化一旦抛错
        # 会中断 stop() 收尾并使 run 卡在 running。异常只 warning 并退回不含富化字段的基础 metrics，无论如何
        # 都照常 finalize_run，绝不阻断真实资金平仓后的落库收尾（保持既有 best-effort 落库风格）。
        try:
            metrics["equityDigest"] = _downsample_equity(self._equity, cap=_EQUITY_DIGEST_MAX)
            metrics["decisionsDigest"] = _digest_decisions(self._decisions, cap=_DECISIONS_MAX)
            metrics["aiStats"] = _compute_ai_stats(self._decisions, self.rounds_completed)
            # R-07（hold-inertia-fix）：wakeSkips 落 metrics 供会话停止后事后验证 F4c 生效（加法）。
            metrics["aiStats"]["wakeSkips"] = self.wake_skips
            # N5（hold-inertia-observability）：会话级全程最大连续观望——环形样本算不出全程最大值，
            # 与 wakeSkips 同为会话级直取（非 _compute_ai_stats 样本口径）。
            metrics["aiStats"]["maxHoldStreak"] = self.max_hold_streak
            # 利润保护观测（§2.7）：exits 是**事件级**口径（实际产生了平仓成交的次数，P1-1），
            # armedRounds 是**轮级**口径（快照曾处于 armed/breakeven_armed 的轮数）；二者不可互相换算。
            metrics["aiStats"]["profitGuardExits"] = self.profit_guard_exits
            metrics["aiStats"]["profitGuardArmedRounds"] = self.profit_guard_armed_rounds
            # 唤醒调度闭环观测（§4.4）：与 status 的四计数**同读**一份 WakeBacklog / PreviewRunner。
            metrics["aiStats"]["wakeScheduler"] = self._wake.stats(self._preview.stats())
            # 成本记账与门禁观测（§3.8，RV-1）：合并落在**调用点**，`_compute_ai_stats` 一字不动。
            metrics["aiStats"] = self._cost.merge_into(metrics["aiStats"])
            metrics["aiStats"] = self._sizing.merge_into(metrics["aiStats"])   # 同款调用点合并
            metrics["aiStats"] = self._horizon.merge_into(metrics["aiStats"])  # §2.12 验收指标
            metrics["aiStats"].update(ai_stats_keys(self.acceptance_bundle()))  # 验收闭环四键
        except Exception as exc:  # noqa: BLE001 —— 富化对 finalize 是加法且可失败，绝不阻断落库收尾
            logger.warning("ai-trade finalize enrichment skipped for run %s: %s", self.run_id, exc)
            for key in ("equityDigest", "decisionsDigest", "aiStats"):
                metrics.pop(key, None)
        sm = get_sessionmaker()
        async with sm() as s:
            await repo.finalize_run(s, self.run_id, status="stopped", metrics=metrics,
                                    end_ts=end_ts)


# ---------- ai-trade-stats-dashboard §3.3：finalize metrics 富化纯函数（模块级、可单测、绝不抛）----------
_DECISION_REASON_CHARS = 160        # decisionsDigest.reason 截断长度（剥离全文，只留近况提示）
# actionCounts 的规范键（匹配 decision.action 枚举；cancel_order 为限价特性新增动作，M-7）。
_ACTION_KEYS = ("open_long", "open_short", "close", "hold", "cancel_order")
# 反思 fail-closed 的四个基础设施码（hold-inertia-fix F5/R-04）：与模型真实否决（reflection_abort）
# 分流计数——否则一次 529 类过载抖动就会抬高否决率、污染 reflectionAborts/reflectionRuns 验收指标。
# 注意：回测侧 backtest.reflection_aborts 历来是 abort+fail-closed 的**合计口径**且保持零改动；实盘
# aiStats 用本处分流口径。两侧口径差有意保留，不得再引入第三种口径。
# 前端 apps/web/lib/aiTradeObservability.ts 逐字镜像本口径（四码集合 + failClosed 判定独立于
# verdict 存在性的分支位置），改动须双侧同步（hold-inertia-observability O3/R-2）。
_REFLECTION_FAIL_CLOSED_REASONS = frozenset(
    {"reflection_timeout", "reflection_llm_error", "reflection_no_tool", "reflection_invalid"})


def _downsample_equity(records, cap: int) -> list[dict]:
    """权益快照下采样：≤cap 原样，>cap 等距抽样并**保留首尾**（供历史详情画权益 / 回撤图）。

    records 为 ``{ts, equity, drawdown}`` 序列（``self._equity`` deque）；空输入回 ``[]``。
    纯函数、绝不抛（防御性 ``dict(r)`` 拷贝，避免回灌内部引用）。
    """
    items = [dict(r) for r in records]
    n = len(items)
    if cap < 2 or n <= cap:
        return items
    step = (n - 1) / (cap - 1)
    idx = sorted({int(round(i * step)) for i in range(cap)})   # i=0→首、i=cap-1→末，去重后 ≤cap 点
    return [items[i] for i in idx]


def _executed_flag(payload) -> bool:
    """决策是否真正落到成交：跳过轮 / 无成交回执 → False（``traded`` 为 executor 真实成交标记）。绝不抛。"""
    if not isinstance(payload, dict) or payload.get("skipped"):
        return False
    return bool(payload.get("traded", False))


def _digest_decisions(records, cap: int) -> list[dict]:
    """取最近 ≤cap 条决策的紧凑摘要，剥离 signalBoard / analysis 等大字段（供详情近况列表）。空→[]。"""
    recent = list(records)[-cap:] if cap > 0 else []
    out: list[dict] = []
    for d in recent:
        reason = d.get("reason")
        out.append({
            "ts": d.get("ts"), "round": d.get("round"), "action": d.get("action"),
            "executed": _executed_flag(d.get("executed")),
            "confidence": d.get("confidence"),
            "reason": reason[:_DECISION_REASON_CHARS] if isinstance(reason, str) else None,
            "reflectionVerdict": d.get("reflectionVerdict"),   # §2.6：反思裁决随历史近况下发（缺省 None）
        })
    return out


def _compute_ai_stats(records, rounds: int) -> dict:
    """镜像 backtest ``_aggregate_stats`` 口径子集（P2-5）：动作计数 / 观望占比 / 平均置信度 / 降级轮数。

    计数在**保留的 ≤50 条决策样本**上求（``_decisions`` 环形）；``decisionRounds`` 取会话真实完成轮数
    ``rounds``（可 > 样本量）。``actionCounts`` 键匹配 ``decision.action`` 实际枚举；``avgConfidence`` 跳过
    ``None``；``degradedRounds`` 计 ``degradeReason`` 非空条数（``_decisions`` 无布尔 ``degraded`` 字段）。绝不抛。

    反思四计数（hold-inertia-fix F5，加法键）：``reflectionRuns`` 计 ``reflectionVerdict`` 非 None 样本；
    ``reflectionAborts`` **只计模型真实否决**（verdict=="abort" 且 ``degradeReason=="reflection_abort"``）；
    ``reflectionFailClosed`` 计四个基础设施码（R-04 分流——反思轮进入前 degrade 恒为 None，故样本上
    二者可严格区分）；``reflectionConfirms`` 计 ``=="confirm"``。
    """
    decisions = list(records)
    counts = {k: 0 for k in _ACTION_KEYS}
    confidences: list[float] = []
    degraded = 0
    reflection_runs = reflection_confirms = reflection_aborts = reflection_fail_closed = 0
    for d in decisions:
        action = d.get("action")
        if action in counts:
            counts[action] += 1
        conf = d.get("confidence")
        if isinstance(conf, (int, float)):
            confidences.append(float(conf))
        degrade_reason = d.get("degradeReason")
        if degrade_reason:
            degraded += 1
        verdict = d.get("reflectionVerdict")
        if verdict is not None:
            reflection_runs += 1
            if verdict == "confirm":
                reflection_confirms += 1
            elif verdict == "abort" and degrade_reason == "reflection_abort":
                reflection_aborts += 1
        if degrade_reason in _REFLECTION_FAIL_CLOSED_REASONS:
            reflection_fail_closed += 1
    sample = len(decisions)
    return {
        "decisionRounds": int(rounds),
        "actionCounts": counts,
        "holdRate": round(counts["hold"] / sample, 4) if sample else 0.0,
        "avgConfidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        "degradedRounds": degraded,
        "reflectionRuns": reflection_runs,
        "reflectionConfirms": reflection_confirms,
        "reflectionAborts": reflection_aborts,
        "reflectionFailClosed": reflection_fail_closed,
    }
