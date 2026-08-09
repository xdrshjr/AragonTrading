"""AI 自动交易 · 回测运行器与流式作业（ai-auto-trading-backtest §2.2/§2.6）。

**决策脑不变、执行落地与数据源变**（一等命题）：回测复用与实盘**逐字相同**的决策链路（每策略影子
`SimBroker` 回放信号板 + 市场体制 + 共识度 + `AiTradeDecider` + 共识门禁 + 滚动摘要），只把「决策 → 真单」
换成「决策 → `SimBroker` 模拟撮合（次根开盘成交、无未来函数）」、把「WS 实时 K 线」换成「历史区间逐根」。
本运行器**不改也不触碰**实盘下单路径（`session.py`/`manager.py`/`live_auto/executor.py`）——回测没有真实
资金面，因此不校验 AI/LIVE 双闸、就绪账户、live-auto 互斥、无既有持仓门禁。

事件循环友好（R4）：重同步 setup（`candles_to_df` + 逐策略 `prepare`/`attach_timeframes` + 暖机回放种影子）
经 `run_in_threadpool` 卸载；逐根 `fill/apply_risk/mark` 循环周期性 `await asyncio.sleep(0)` 让出；仅
`decider.decide`/`summarize` 的 LLM `await` 内联。流式生命周期（订阅/快照/取消/终态）镜像 `WeightTrainManager`
的经测骨架，但采**有界环形缓冲**（决策近 200、进度只留最新）控长回测内存——完整数组走落库与完成 `result`。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, replace

import pandas as pd
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from ..agent.llm.factory import make_llm_client_from_config
from ..config import Settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.backtester import candles_to_df
from ..engine.benchmark import compute_buy_hold
from ..engine.indicators import atr
from ..engine.metrics import compute_metrics
from ..engine.metrics_ext import compute_extended_metrics
from ..engine.mtf import bar_to_seconds
from ..engine.simulator import RiskParams, SimBroker, TradeRecord
from ..okx.types import BAR_SECONDS
from ..paper.strategy_factory import PaperStrategySpec
from ..strategies.base import Signal
from ..agent.services import load_htf_frames, load_range_candles
from .backtest_frames import build_backtest_frames
from .base_rules import MaBandRule, apply_ma_band_gate, build_ma_band, format_charter
from .cost_model import (
    backtest_params,
    estimate_round_trip_cost,
    funding_periods,
    query_for_preview,
)
from .decider import AiTradeDecider, TradeDecision
from .edge_gate import evaluate_edge_gate
from .exit_attribution import (ExitLedger, classify_backtest, exit_counters,
                               is_deterministic_reason)
from .funding import FundingSnapshot
from .funding_ledger import BacktestFundingCurve, FundingLedger
from .hold_horizon import HorizonFacts, HorizonRule
from .hold_horizon import rule_wire as horizon_rule_wire
from .hold_horizon_text import format_backtest_notes
from .hold_horizon_text import format_charter as format_horizon_charter
from .horizon_acceptance import ai_stats_keys, build_input, bundle_from_input
from .horizon_runtime import HorizonRuntime
from .market_context import build_market_context
from .market_digest import build_market_digest
from .multi_timeframe_context import (
    build_multi_timeframe_context,
    decision_timeframes,
)
from .profit_cooldown import (
    CooldownState,
    apply_profit_cooldown,
    build_cooldown_view,
)
from .profit_guard import (
    PositionFacts,
    ProfitGuardRule,
    ProfitGuardState,
    build_snapshot,
    clamp_commitment,
    is_commit_armed,
    ratchet_commitment,
    rule_wire,
    sanitize_atr,
)
from .profit_guard_bar import BarFacts, evaluate_bar
from .profit_guard_text import format_profit_guard_charter
from .prompts import build_ai_trade_system_prompt
from .reflection import apply_reflection, build_reflection_note
from .signals import (
    Consensus,
    StrategySignalView,
    apply_consensus_gate,
    compute_consensus,
    decision_to_signals,
    evaluate_signal_board,
    make_shadow_broker,
)
from .sizing_runtime import SizingContext, build_sizing_runtime
from .sizing_text import SIZING_BACKTEST_NOTE
from .turnover_discipline import rule_from_horizon as turnover_rule_from_horizon

logger = logging.getLogger(__name__)

_REGIME_MIN_BARS = 60          # engine/regime 单点体制判定最小根数（暖机须覆盖）
_YIELD_EVERY_BARS = 16         # 逐根循环每 N 根 await sleep(0) 让出事件循环（R4）
_PROGRESS_EVERY_BARS = 2       # 进度事件节流：每 N 根推一次（外加末根必推）
_DECISIONS_BUFFER_MAX = 200    # 决策环形缓冲上限（重连仅回放近端，全量走落库/result）
_SUMMARIES_BUFFER_MAX = 64     # 摘要缓冲上限
_EQUITY_BUFFER_MAX = 1000      # 权益缓冲上限（滚动迷你图重连回放）
_TERMINAL_TYPES = ("done", "cancelled", "error")
# 回测保护层的**保守近似免责**（adaptive §2.3.5）：与前端 aiBacktest/panels.tsx 同源文案。绝不宣称
# 等同实盘——回测接入保护层最大的风险恰恰是「曲线变好看 ⇒ 用户高估实盘」（R-4）。
_PROFIT_GUARD_BACKTEST_NOTE = (
    "利润保护在回测中为 bar 内极值保守近似：① 同一根内的新高不会立即抬高移动止盈线（先判回撤、"
    "后推峰值）；② 跳空穿越按开盘价成交；③ 回测按根推进，实盘每 5 秒盯市一次，触发时机必然更早、"
    "更细；④ 回测无部分成交、无挂单撤改、无交易所降级态；⑤ 开仓当根不判回撤（min_hold 在 bar 时钟下"
    "等价于「至少持有一根」），且冷却秒数若短于一根 K 线（默认 180s < 15m）在回测里几乎恒于次根即已"
    "过期——回测无法验证冷却期的实际效果。故回测的保护触发次数与落袋结果相对实盘偏保守，"
    "不得据此推断实盘一定更好或更差。"
    # C5 §2.5：承诺退出线同样为 bar 内极值保守近似，且回测里的 holdExitPx 来自**回测 LLM** 的决策，
    # 与实盘的模型判断可能不同——回测用于验证「承诺线一旦给出、系统会如何兑现」的机制，不保证实盘
    # 模型会给出相同的线。
    "⑥ 承诺退出线同样为 bar 内极值保守近似，且回测中的 holdExitPx 来自回测 LLM 的决策、与实盘可能"
    "不同——回测验证的是「承诺线一旦给出系统如何兑现」的机制，不保证实盘模型会给出相同的线。")
# 最小 edge 门禁的**粗估免责**（§8 R-3）：绝不让用户把拦截率当成胜率证据。
_EDGE_GATE_BACKTEST_NOTE = (
    "阶段一的 edge 为 ATR 粗估（min(ATR 预算, 单仓 OCO 止盈距离)），**不是**概率化胜率先验；"
    "门禁拦截率仅作结构性参考，不得据此推断策略的真实期望。回测世界的往返成本按 SimBroker 实收的"
    "双边费 + 双边滑点计（与实盘常数各自独立），故两侧的拦截率不可直接比较。")


@dataclass
class AiBacktestSpec:
    """启动一个 AI 回测作业的全部已解析输入（聚合传参，满足 CLAUDE.md ≤5 形参）。"""

    inst_id: str
    bar: str
    strategy_ids: list[int]
    strategy_names: list[str]
    specs: list[PaperStrategySpec]
    start_ts: int
    end_ts: int
    decision_every_bars: int
    initial_capital: float
    leverage: float
    stop_loss_pct: float | None
    take_profit_pct: float | None
    take_profit_usd: float | None
    stop_loss_usd: float | None
    context_refresh_rounds: int
    min_consensus: int
    planned_rounds: int
    settings: Settings
    rest: object                # OKX rest client（取历史区间用）
    provider: object            # LlmProviderConfig（造决策 LLMClient 用）
    position_step_pct: float = 0.0   # 仓位操作档位步长（fraction；0=连续，直接构造该 dataclass 的既有测试零回归）
    reflection_enabled: bool = False   # 下单前二次反思（默认关 → 直接构造该 dataclass 的既有回测测试零回归，RV-1）
    # 【基础策略】MA 通道开单铁律（ai-trade-base-strategy-ma-band §2.8/P1-4）：与实盘**共用同一纯核心**，
    # 严禁在本文件复制一份判定逻辑。默认 None=不装配 ⇒ payload 与 prompt 逐字节回退（既有回测测试零回归）。
    base_rule: MaBandRule | None = None
    # 利润保护层（adaptive §2.3/F3）：同样与实盘**共用同一纯核心**（`profit_guard` + `profit_guard_bar`
    # + `profit_cooldown`），差异只在「谁提供行情断面」。默认 None=不装配 ⇒ `_run_loop` 的 SimBroker
    # 调用序与 payload/prompt 逐字节回退（G-3，既有直接构造该 dataclass 的回测测试零回归）。
    profit_rule: ProfitGuardRule | None = None
    # 交易周期模式（holding-horizon §2.10）：与实盘**共用同一纯核心**（`hold_horizon` +
    # `horizon_runtime`），差别只在时钟与断面来源（墙钟 → bar ts，实时价 → 当根收盘价）。默认
    # None=不装配 ⇒ payload / prompt / 门禁 / notes 逐字节回退（既有回测测试零回归）。
    horizon_rule: HorizonRule | None = None


# ============================ 线格式序列化助手（纯函数） ============================
def _view_full(v: StrategySignalView) -> dict:
    """决策 payload 用的完整信号板视图（含策略风控，供 prompt 综合研判）。"""
    risk = v.risk_params
    return {"strategyId": v.strategy_id, "name": v.name, "stance": v.stance,
            "latestSignal": v.latest_signal, "strength": round(v.strength, 4),
            "reason": v.reason, "stopLossPct": risk.stop_loss_pct,
            "takeProfitPct": risk.take_profit_pct, "maxHoldBars": risk.max_hold_bars}


def _view_slim(v: StrategySignalView) -> dict:
    """决策 WS 事件用的精简信号板视图。"""
    return {"strategyId": v.strategy_id, "name": v.name, "stance": v.stance,
            "latestSignal": v.latest_signal, "strength": round(v.strength, 4), "reason": v.reason}


def _consensus_wire(c: Consensus) -> dict:
    """`Consensus` dataclass → camelCase wire dict（供 prompt 与 decision 事件）。"""
    return {"longVotes": c.long_votes, "shortVotes": c.short_votes, "flatVotes": c.flat_votes,
            "total": c.total, "netStance": c.net_stance, "agreement": c.agreement}


def _trade_wire(t: TradeRecord) -> dict:
    """`TradeRecord` → camelCase wire dict（图表 marker + result.trades）。"""
    return {"ts": t.ts, "side": t.side, "price": t.price, "qty": t.qty, "fee": t.fee, "pnl": t.pnl,
            "positionAfter": t.position_after, "reason": t.reason,
            "stopLossPrice": t.stop_loss_price, "takeProfitPrice": t.take_profit_price,
            "stopLossPct": t.stop_loss_pct, "takeProfitPct": t.take_profit_pct,
            "maxHoldBars": t.max_hold_bars}


def _project_side(current: str, action: str) -> str:
    """决策落地后的预计立场（供 executed.positionAfter；实际成交在次根开盘）。"""
    if action == "open_long":
        return "long"
    if action == "open_short":
        return "short"
    if action == "close":
        return "flat"
    return current


class AiBacktestRunner:
    """async 逐根 AI 回测运行器：取数→暖机→逐根 fill/risk/mark→决策根跑 AI→finalize+指标+落库。"""

    def __init__(self, spec: AiBacktestSpec):
        self.inst_id = spec.inst_id
        self.bar = spec.bar
        self.strategy_ids = spec.strategy_ids
        self.strategy_names = spec.strategy_names
        self.specs = spec.specs
        self.start_ts = spec.start_ts
        self.end_ts = spec.end_ts
        self.decision_every_bars = spec.decision_every_bars
        self.initial_capital = spec.initial_capital
        self.leverage = spec.leverage
        self.take_profit_usd = spec.take_profit_usd
        self.stop_loss_usd = spec.stop_loss_usd
        self.context_refresh_rounds = spec.context_refresh_rounds
        self.min_consensus = spec.min_consensus
        self.planned_rounds = spec.planned_rounds
        self.max_rounds = spec.settings.ai_backtest_max_rounds
        self.settings = spec.settings
        self.rest = spec.rest
        self.position_step_pct = spec.position_step_pct   # 档位吸附（实盘/回测经共享决策脑单点收口，§2.3）
        # 下单前二次反思（RV-1：经数据类字段传参，__init__ 一次性构造决策器时接收；默认关 → 直接构造
        # AiBacktestSpec/Runner 且用默认 Settings 的既有回测测试零回归，不平白多一次未脚本化反思调用）。
        self.reflection_enabled = spec.reflection_enabled
        # 【基础策略】铁律：回测与实盘**同源生效**（P1-4）——否则 AI 回测会系统性地比实盘更容易开仓，
        # 用户据此调出的参数在实盘直接失真。规则来源与实盘同一个 `_resolve_base_rules`。
        self.base_rule = spec.base_rule
        # 利润保护层（F3）：规则、逐根状态、冷却态与触发计数。规则为 None ⇒ 全部路径空转（G-3）。
        self.profit_rule = spec.profit_rule
        self._guard_state: ProfitGuardState | None = None
        self._cooldown: CooldownState | None = None
        # C2/C5：承诺退出线棘轮状态。[P0-1] **必须在每处 `self._guard_state=None` 同步清零**——否则
        # 回测里反手/重开会继承旧承诺线、与实盘同缺陷（见 `_apply_profit_guard_bar` 两处复位）。
        self._committed_exit_px: float | None = None
        self._committed_exit_basis: str | None = None
        self.profit_guard_exits = 0
        self._atr_series = None            # `_setup` 里一次性向量化；第 i 根用 iloc[i-1]（无未来函数）
        self._bar_ts_index: list[int] | None = None   # 根索引 → ts（把开仓根 ts 当 opened_wall，§2.3.2）
        # 交易周期层（holding-horizon §2.10）：与实盘同一个运行时类，只是喂进去的时钟是 bar ts。
        self.horizon_rule = spec.horizon_rule
        # `record_exits=False`：回测的退出来源必须**独立于周期模式的开关**——`_track_horizon_epoch`
        # 首行就是 `if self.horizon_rule is None: return`，挂在它里面会让「关掉周期模式跑对照臂」
        # 这件 L1 必做的事拿不到任何退出分布（[P0-4] / [P1-4] 同款连坐）。
        # 开仓侧换手纪律（turnover-budget §4.5）：回测侧**独立总闸**，关 ⇒ 传 None ⇒ 两道新闸
        # 整层空转、`gate_reason` 逐字回退（G-3）。轴 B（兜底核实）**不在回测装配**（D-5）。
        self._horizon = HorizonRuntime(
            spec.horizon_rule, record_exits=False,
            turnover_rule=(turnover_rule_from_horizon(spec.horizon_rule, spec.settings)
                           if spec.settings.ai_trade_horizon_turnover_backtest_enabled
                           else None))
        self._horizon_hold_bars = (None if spec.horizon_rule is None
                                   else spec.horizon_rule.expected_hold_bars)
        # 验收闭环三本账（horizon-acceptance）：与实盘共用同一批纯核心，只是喂进去的时钟是 bar ts。
        # `net_mode=True`：`SimBroker` 的平仓 `TradeRecord.pnl` **已扣双边费**（`_close_at` 里
        # `net_pnl = gross - (entry_fee + exit_fee)`），而 `fee` 只是平仓腿——搞反会让净额多减一次费。
        self._exits = ExitLedger(net_mode=True)
        # `ct_val=1.0`：`SimBroker` 的 `qty` 本来就是币本位（`qty = notional / fill`），
        # 不像实盘账本那样按张数计（实盘要乘 ctVal）。
        self._funding = FundingLedger(
            enabled=bool(spec.settings.ai_trade_funding_ledger_enabled)
            and bool(spec.settings.ai_trade_funding_backtest_enabled), ct_val=1.0)
        self._curve = BacktestFundingCurve([])
        # 轴 B [P2-1]：曲线起点之前的若干根 `snapshot_at` 恒 `None`，故「本次回测含不含资金费」
        # 不是一个全局布尔——如实计「含资金费的决策轮 / 总决策轮」并写进 notes。
        self._funding_cost_rounds = self._funding_cost_covered = 0
        # 单槽打标：凡是运行时**自己**提交的平仓在 submit 之前写槽，成交被观察到时消费。
        # `SimBroker` 自己的盘中止损止盈 / max_hold / 区间收尾不经运行时 ⇒ 槽为空、由 reason 兜住。
        self._pending_exit_source: str | None = None
        self._stamp_conflicts = 0        # 槽非空但 reason 命中确定性字面量的次数（R-6，进 notes）
        self._trade_scan_idx = 0         # 已归因到的 `main.trades` 游标
        self._entry_ts: int | None = None    # 当前持仓的开仓根 ts（退出样本的持仓时长起点）
        self._leg_fee = 0.0              # 自上一笔平仓（不含）起累计的腿费（一笔往返的总费用）
        self._lifetime = {"trips": 0, "gross": 0.0, "fee": 0.0, "net": 0.0,
                          "absGross": 0.0}
        self._wall_seconds = 1.0         # [P1-3] 回测口径：**首决策根 → 末根**的模拟跨度

        self.warmup = _compute_warmup(self.specs, spec.settings.ai_trade_regime_context)
        # allow_limit 恒 False（ai-trade-limit-orders §2.10，Non-goal）：`SimBroker._pending` 是「次根
        # 开盘无条件成交」的一根队列、`_Order` 连价格字段都没有。放任 Agent 在回测里挂限价而每单必成，
        # 会**系统性高估**限价策略——这比「回测不支持限价」严重得多。故回测里 Agent 根本看不到
        # orderType/limitPrice，只能市价：零分歧、诚实。此处显式写出（而非依赖默认值）以表明是决策。
        self.decider = AiTradeDecider(
            make_llm_client_from_config(spec.provider),
            build_ai_trade_system_prompt(self.inst_id, self.bar, self.initial_capital, self.leverage,
                                         position_step_pct=self.position_step_pct,
                                         allow_limit=False,
                                         # 铁律宪章同源注入；未装配 → "" ⇒ 渲染逐字节不变
                                         base_rules_text=format_charter(self.base_rule),
                                         # 利润保护宪章同源注入（F3）；未装配 → "" ⇒ 同上零回归
                                         profit_guard_text=format_profit_guard_charter(
                                             self.profit_rule,
                                             horizon_armed=self.horizon_rule is not None),
                                         # 交易周期宪章同源注入；未装配 → "" ⇒ 同上零回归。解除阈按
                                         # 本次回测真实的 OCO 止损与杠杆现算，绝不写死算例。
                                         horizon_text=format_horizon_charter(
                                             self.horizon_rule,
                                             stop_loss_pct=spec.stop_loss_pct,
                                             take_profit_pct=spec.take_profit_pct,
                                             leverage=self.leverage)),
            context_refresh_rounds=self.context_refresh_rounds, settings=self.settings,
            position_step_pct=self.position_step_pct, reflection_enabled=self.reflection_enabled,
            allow_limit=False,
            # C5 §2.5：回测决策器同样露出 holdExitPx（allow_limit 恒 False 不变）——承诺退出线是决策
            # 语义、与限价的「回测每单必成」高估问题无关，故只随 commit_enabled 派生。
            allow_hold_exit=(self.profit_rule is not None and self.profit_rule.commit_enabled))
        self._oco_risk = RiskParams(
            stop_loss_pct=spec.stop_loss_pct or None, take_profit_pct=spec.take_profit_pct or None,
            max_hold_bars=self.settings.ai_trade_default_max_hold_bars or None)
        self.main = SimBroker(self.initial_capital,
                              taker_fee_bps=self.settings.default_taker_fee_bps,
                              slippage_bps=self.settings.default_slippage_bps, leverage=self.leverage)
        self.main.set_risk(self._oco_risk)
        # 仓位/刹车层（vol-sizing §3.10）：与实盘共用同一批纯核心，规则由 runner 从 settings 自解析（[P1-7]）。
        self._sizing = build_sizing_runtime(spec.settings, self.inst_id, BAR_SECONDS[self.bar])
        self.shadows = [make_shadow_broker() for _ in self.specs]

        self.rounds_done = 0
        # M-8：动作字典须容纳新动作（回测 allow_limit=False ⇒ cancel_order 恒被 `_map_decision` 判为
        # invalid_action，该计数恒 0；但字典不该在新动作出现时才被动扩张）。
        self.action_counts: dict[str, int] = {"open_long": 0, "open_short": 0, "close": 0,
                                              "hold": 0, "cancel_order": 0}
        self.degraded_rounds = 0
        self.timeout_rounds = 0
        # 下单前二次反思观测计数（§2.5；reflection_enabled=False 时恒 0，逐字等价旧行为）。
        self.reflection_runs = 0          # 真跑过反思的轮数（交易意愿轮）
        self.reflection_aborts = 0        # 反思否决（abort / fail-closed）→ 降级 hold 的轮数
        self.reflection_adjustments = 0   # 反思确认但收缩了仓位的轮数
        self.summary_count = 0
        self._confidences: list[float] = []
        self._reached_cap = False
        self._session_exited = False
        self._exit_reason: str | None = None
        self._trades_emitted = 0
        self._bars_processed = 0
        self._bars_in_market = 0                      # 在场根计数（供 timeInMarketPct 权威分子）
        self._last_ts = 0
        self._last_close = 0.0
        self._decisions_digest: deque[dict] = deque(maxlen=_DECISIONS_BUFFER_MAX)
        self._summaries_all: deque[dict] = deque(maxlen=_SUMMARIES_BUFFER_MAX)

    # ---------- 顶层驱动 ----------
    async def run(self, job: "_BacktestJob") -> None:
        """整段回测：setup（取数/建帧/暖机，卸载重同步件）→ 逐根循环 → finalize 落库；异常收口为终态。"""
        try:
            setup = await self._setup(job)
            if setup is None:                        # setup 期间被取消
                job.mark_terminal("cancelled", {"jobId": job.job_id})
                return
            main_df, dfs, context_frames, first = setup
            job.publish({"type": "phase", "data": {"phase": "running"}})
            state = await self._run_loop(job, main_df, dfs, context_frames, first)
            job.publish({"type": "phase", "data": {"phase": "finalizing"}})
            await self._finalize(job, main_df, first, state)
        except asyncio.CancelledError:
            job.mark_terminal("cancelled", {"jobId": job.job_id})
            raise
        except HTTPException as exc:
            code, message = _http_detail(exc)
            job.mark_terminal("failed", {"code": code, "message": message, "jobId": job.job_id})
        except Exception as exc:  # noqa: BLE001 —— 作业内任何失败收口为 failed 终态，绝不卡 running
            logger.warning("ai-backtest job %s failed: %s", job.job_id, exc)
            job.mark_terminal("failed", {"code": "AI_BACKTEST_FAILED",
                                         "message": "回测执行异常", "jobId": job.job_id})

    # ---------- setup（重同步件经 run_in_threadpool 卸载，R4）----------
    async def _setup(
        self,
        job: "_BacktestJob",
    ) -> tuple[pd.DataFrame, list[pd.DataFrame], dict[str, pd.DataFrame], int] | None:
        job.publish({"type": "phase", "data": {"phase": "loading"}})
        bar_sec = bar_to_seconds(self.bar)
        left = self.start_ts - self.warmup * bar_sec
        range_rows = await load_range_candles(
            self.rest, inst_id=self.inst_id, bar=self.bar, start_ts=left, end_ts=self.end_ts,
            max_bars=self.settings.backtest_max_bars)
        htf_frames = await self._load_htf(left, bar_sec)
        if job.cancelled:
            return None
        main_df = candles_to_df(range_rows)
        dfs = await run_in_threadpool(build_backtest_frames, self.specs, range_rows, htf_frames,
                                      self.bar)
        context_frames = {
            bar: candles_to_df(htf_frames[bar])
            for bar in (decision_timeframes(self.bar)
                        if self.settings.ai_trade_multi_timeframe_context else ())
            if htf_frames.get(bar)
        }
        first = self._first_decision_index(main_df)
        # [P1-3] 墙钟口径的分母 = **首决策根 → 末根**的模拟跨度：暖机段既不决策也不持仓，算进去
        # 会把每日换手次数稀释成一个恒过的门槛。
        self._wall_seconds = max(
            1.0, float(int(main_df.iloc[-1]["ts"]) - int(main_df.iloc[first]["ts"])))
        # 根索引 → ts：退出样本的持仓时长与资金费 epoch 都按真实 ts 计，**与周期模式是否装配无关**，
        # 故从下面那个条件里提出来无条件建（它只是一个 int 列表，代价可忽略）。
        self._bar_ts_index = main_df["ts"].astype(int).tolist()
        await self._load_funding_curve()
        # 最小 edge 门禁同样按 `_atr_at(i)` 取粗估 edge（§3.6），故 k_min>0 时也必须建这条序列——
        # 否则门禁恒 unavailable、fail-open 放行（I-5）。ATR 止损（vol-sizing §3.10 复发点一）同款：不把
        # 总闸②或进来，`_atr_series` 恒 None ⇒ 止损在回测里恒不生效**且不报任何错**（T-19 守这条）。
        if (self.profit_rule is not None or self.settings.ai_trade_edge_gate_k_min > 0
                or self.settings.ai_trade_stop_atr_mult > 0
                or self.horizon_rule is not None):
            # F1/F3：ATR 一次性向量化（第 i 根取 iloc[i-1]，含本根 h/l 的 ATR 是未来函数）。
            # 交易周期层同样要 `_bar_ts_index`——`opened_wall` 取开仓根 ts，缺这条索引会让持仓时长
            # 恒为 0、G1 在回测里恒不生效（`_atr_series` 是顺带的，本层不读它）。
            self._atr_series = atr(main_df, self.settings.ai_trade_profit_guard_atr_period)
        job.publish({"type": "phase", "data": {"phase": "priming"}})
        await run_in_threadpool(self._prime_shadows, dfs, first)
        if job.cancelled:
            return None
        return main_df, dfs, context_frames, first

    async def _load_funding_curve(self) -> None:
        """拉一次覆盖 `[start_ts, end_ts]` 的真实历史资金费曲线（horizon-acceptance §2.2.4）。

        取不到 ⇒ 曲线 `available=False` ⇒ 资金费按「口径不可得」呈现、验收卡第 5 行判样本不足，
        **绝不按 0 呈现**（回测里资金费恒 0 正是本轮要修的那件事）。异常一律吞掉：回测的主结果
        不该因为一个观测层的公共端点抖动而失败。
        """
        if not self._funding.enabled:
            return
        rows: list[dict] = []
        after_ms: int | None = (self.end_ts + 1) * 1000
        try:
            for _ in range(int(self.settings.ai_trade_funding_history_max_pages)):
                page = await self.rest.get_funding_rate_history(
                    self.inst_id, after_ms=after_ms, limit=100)
                if not page:
                    break
                rows.extend(page)
                oldest = page[0]["fundingTs"]
                if oldest <= self.start_ts:
                    break
                after_ms = oldest * 1000
        except Exception as exc:  # noqa: BLE001 —— 观测层取数失败绝不打断回测主流程
            logger.warning("ai-backtest funding curve unavailable (%s): %s",
                           self.inst_id, exc)
        self._curve = BacktestFundingCurve(rows)

    async def _load_htf(self, left: int, base_sec: int) -> dict:
        strategy_tfs = {
            tf for spec in self.specs for tf in spec.strategy.timeframes()
            if tf != self.bar and bar_to_seconds(tf) > base_sec
        }
        context_tfs = (set(decision_timeframes(self.bar))
                       if self.settings.ai_trade_multi_timeframe_context else set())
        tfs = sorted(strategy_tfs | context_tfs,
                     key=bar_to_seconds)
        if not tfs:
            return {}
        strategy_warmup = max(
            (spec.strategy.htf_warmup_bars() for spec in self.specs), default=60)
        max_warmup = max(80, strategy_warmup)
        return await load_htf_frames(self.rest, inst_id=self.inst_id, start_ts=left,
                                     end_ts=self.end_ts, base_seconds=base_sec, timeframes=tfs,
                                     htf_warmup_bars=max_warmup)

    def _first_decision_index(self, main_df: pd.DataFrame) -> int:
        """首个可决策根：既过暖机（`i≥warmup`）又落在用户区间内（`ts≥start_ts`）。"""
        n = len(main_df)
        first_ts = next((i for i in range(n) if int(main_df.iloc[i]["ts"]) >= self.start_ts), n - 1)
        return max(self.warmup, first_ts)

    def _prime_shadows(self, dfs: list[pd.DataFrame], first: int) -> None:
        """暖机段逐根种入每策略影子内部状态（不决策、主账户恒 flat）；同步，跑在线程池。"""
        limit = min(first, len(dfs[0])) if dfs else 0
        for i in range(limit):
            evaluate_signal_board(self.specs, self.shadows, dfs, i)

    # ---------- 逐根循环 ----------
    async def _run_loop(self, job: "_BacktestJob", main_df: pd.DataFrame,
                        dfs: list[pd.DataFrame], context_frames: dict[str, pd.DataFrame],
                        first: int) -> str:
        """镜像 run_backtest 的 fill/risk/mark 四阶段，把 on_candle→submit 换成 AI 决策→submit。"""
        n = len(main_df)
        total = max(1, n - first)
        for i in range(first, n):
            if job.cancelled:
                return "cancelled"
            row = main_df.iloc[i]
            self.main.fill_pending(open_price=float(row["o"]), ts=int(row["ts"]), bar_index=i)
            self.main.apply_risk(i, row, self._oco_risk)
            # ★ 利润保护（F3 §2.3.3）：排在 apply_risk **之后**（交易所侧 OCO 物理上先于本进程存在，
            # 同根同时命中按更保守的先手记因）、mark **之前**（本根内的平仓必须反映进本根权益点）。
            self._apply_profit_guard_bar(i, row)
            self._track_horizon_epoch(int(row["ts"]))
            # 验收闭环三本账（horizon-acceptance）：**独立于 `_track_horizon_epoch`**，故未装配
            # 周期模式的对照臂照样拿得到资金费与退出分布（[P1-4] / [P0-4]）。
            self._track_ledgers(int(row["ts"]), float(row["c"]))
            self.main.mark(ts=int(row["ts"]), close=float(row["c"]))
            self._sizing.note_equity(self.main.equity_at(float(row["c"])))   # 刹车权益推进
            if self.main.pos.side != "flat":         # [B1] 在场根计数（timeInMarketPct 分子）
                self._bars_in_market += 1
            self._bars_processed = i - first + 1
            self._last_ts, self._last_close = int(row["ts"]), float(row["c"])
            self._emit_new_trades(job)
            board = evaluate_signal_board(self.specs, self.shadows, dfs, i)  # 每根步进影子（快，无 LLM）
            self._maybe_session_exit(row, i, n)
            if not self._session_exited and self._is_decision_bar(i, first, n):
                await self._run_decision(job, main_df, context_frames, i, board)
                self._emit_new_trades(job)
            self._emit_progress(job, i, first, total, row)
            if (i - first) % _YIELD_EVERY_BARS == 0:
                await asyncio.sleep(0)               # R4：周期让出，避免拖住其它 WS/请求
        return "completed"

    # ---------- 利润保护（F3：与实盘共用纯核心，本文件只提供行情断面）----------
    def _atr_at(self, i: int) -> float | None:
        """第 `i` 根可用的 ATR：取 `iloc[i-1]`（含本根 h/l 的 ATR 是未来函数）。

        `i-1 < 0`、`NaN`（`ewm(min_periods=period)` 的前 `period-1` 行恒为 NaN，而暖机在「无 HTF +
        关闭体制上下文 + 策略暖机很小」的组合下可以小于 14）、≤0 一律 `None` ⇒ 上下界整体不生效，
        与实盘走**同一条降级判据** `sanitize_atr`（P2-5）。
        """
        if self._atr_series is None or i - 1 < 0:
            return None
        return sanitize_atr(self._atr_series.iloc[i - 1])

    def _reset_guard_epoch(self) -> None:
        """[P0-1] epoch 结束（flat / 保护平仓）：state 与承诺线棘轮**同步三清**（镜像 runtime.clear_state
        的三清）——否则回测里反手/重开会继承旧承诺线、与实盘同缺陷（T-6 回测侧对称覆盖）。"""
        self._guard_state = None
        self._committed_exit_px = None
        self._committed_exit_basis = None

    def _apply_profit_guard_bar(self, i: int, row: pd.Series) -> None:
        """本根的保守保护判定；命中即按触发价平仓并开启冷却。未装配 / 空仓 ⇒ 完全空转（G-3）。"""
        if self.profit_rule is None:
            return
        pos = self.main.pos
        if pos.side == "flat":
            self._reset_guard_epoch()
            return
        bar_ts = int(row["ts"])
        entry_ts = self._bar_ts(pos.entry_bar, default=bar_ts)
        result = evaluate_bar(self._guard_state, self.profit_rule, BarFacts(
            seq=pos.entry_bar, side=pos.side, entry_px=float(pos.entry_price),
            qty_coin=float(pos.qty), leverage=self.leverage, ts=bar_ts, opened_ts=entry_ts,
            open_px=float(row["o"]), high_px=float(row["h"]), low_px=float(row["l"]),
            atr_px=self._atr_at(i), committed_exit_px=self._committed_exit_px))
        self._guard_state = result.state
        if result.verdict is None:
            return
        self._pending_exit_source = "profitGuard"    # 轴 B 打标槽（submit 之前写）
        self.main.close_at_price(float(result.fill_px), bar_ts, result.verdict.reason_code)
        self.profit_guard_exits += 1
        self._reset_guard_epoch()
        if self.profit_rule.cooldown_s > 0:
            # F2 §2.3.3a：写点唯一——**确实产生了保护性平仓**之后才开冷却（bar ts 当墙钟）。
            self._cooldown = CooldownState(
                until_wall=bar_ts + self.profit_rule.cooldown_s, side=result.verdict.note["side"],
                reason_code=result.verdict.reason_code, label=result.verdict.label,
                scope=self.profit_rule.cooldown_scope)

    def _record_commitment(self, decision: TradeDecision, snapshot: dict | None) -> None:
        """回测侧承诺退出线采集（C5 §2.5）：与实盘 `record_commitment` **同一** `is_commit_armed` +
        `clamp_commitment`（纯几何）+ `ratchet_commitment`（唯一棘轮真源）——同 import、同阈值、同棘轮，
        绝不在此内联另写收紧比较（[P1-1]/[P1-2]）。未装配 / 承诺关 / 非 hold / 无值 / 无快照 / 未武装 ⇒ 短路。"""
        if (self.profit_rule is None or not self.profit_rule.commit_enabled
                or decision.action != "hold" or decision.hold_exit_px is None
                or not snapshot or not is_commit_armed(snapshot.get("status"))):
            return
        line, basis = clamp_commitment(
            decision.hold_exit_px, side=snapshot.get("side"), mark_px=snapshot.get("livePx"),
            entry_px=snapshot.get("entryPx"), breakeven_px=snapshot.get("breakevenPx"),
            atr_px=snapshot.get("atrPx"), atr_floor_mult=self.profit_rule.atr_floor_mult)
        self._committed_exit_px, self._committed_exit_basis = ratchet_commitment(
            self._committed_exit_px, self._committed_exit_basis, line, basis,
            side=snapshot.get("side"))

    def _bar_ts(self, bar_index: int, *, default: int) -> int:
        """开仓根 ts（`state.opened_wall` 的来源）；索引越界 / 未记录 ⇒ 回落当前根（⇒ holdSeconds=0）。"""
        if bar_index is None or bar_index < 0 or self._bar_ts_index is None:
            return default
        if bar_index >= len(self._bar_ts_index):
            return default
        return int(self._bar_ts_index[bar_index])

    # ---------- 交易周期层（holding-horizon §2.10：与实盘共用纯核心，本文件只提供 bar 时钟）----------
    def _track_horizon_epoch(self, bar_ts: int) -> None:
        """逐根维护 epoch：开仓即锁 `opened_wall = 开仓根 ts`，转 flat 即收一条持仓时长样本。

        **绝不依赖 `refresh()` 的兜底建 epoch**：那条路径会把 `opened_wall_estimated` 置真，从而让
        `epoch_estimated` 豁免每一轮都命中 ⇒ G1 在回测里恒不生效且不报任何错（与实盘结论相反）。
        实盘的对应真源是 `_dispatch` 的开仓成交事件，回测这里是**开仓根 ts**（§2.10 对照表）。
        """
        if self.horizon_rule is None:
            return
        pos = self.main.pos
        if pos.side == "flat":
            if self._horizon.state.epoch is not None:
                self._horizon.note_close({}, float(bar_ts))
            return
        epoch = (pos.side, int(pos.entry_bar))
        current = self._horizon.state.epoch
        # 反手（long → short）在**同一根**里换了身份、中间不经过 flat：不先收一笔 `note_close`，上一段
        # 持仓的时长样本与 `roundTrips` 会静默消失，而反手恰是本层专门计数的一类换手（reverseVetoes）。
        # 实盘侧无此问题——`_dispatch` 的 close 与 open 是两条成交事件，天然成对。
        if current is not None and current != epoch:
            self._horizon.note_close({}, float(bar_ts))
        self._horizon.note_open({"side": pos.side},
                                float(self._bar_ts(pos.entry_bar, default=bar_ts)),
                                seq=int(pos.entry_bar))

    # ---------- 验收闭环三本账（horizon-acceptance；与实盘共用纯核心，本文件只提供 bar 时钟）----------
    def _track_ledgers(self, bar_ts: int, close_px: float) -> None:
        """逐根推进资金费 / 退出来源 / 全程成本三本账。

        **门控只有各自的总闸**，与 `horizon_rule` 无关——「装 vs 不装周期模式的对照回测」正是
        L1 要做的事，把它们挂在周期模式的开关下就等于把对照臂的数据抹掉。
        """
        for trade in self._drain_new_trades():
            if trade.side in ("long", "short"):
                self._note_backtest_open(trade)
            elif trade.side == "close":
                self._note_backtest_close(trade)
        # 收尾：给仍在场的 epoch 累计到本根（已平仓的那段在 `_note_backtest_close` 里已结算）。
        self._funding.accrue_bars(self._curve, from_ts=0, to_ts=bar_ts, mark_px=close_px)

    def _drain_new_trades(self) -> list:
        """自上次归因以来新产生的成交（`main.trades` 只增不改，故一个游标即可）。"""
        idx = self._trade_scan_idx
        self._trade_scan_idx = len(self.main.trades)
        return list(self.main.trades[idx:])

    def _note_backtest_open(self, trade) -> None:
        self._entry_ts = int(trade.ts)
        self._leg_fee += float(trade.fee or 0.0)
        self._funding.note_open({"side": trade.side, "qty": float(trade.qty or 0.0)},
                                seq=int(trade.ts), now_wall=float(trade.ts))

    def _note_backtest_close(self, trade) -> None:
        """一笔平仓成交的三本账收口（退出来源 / 全程成本 / 资金费结转）。"""
        # 先把本段持仓**截至平仓时刻**的资金费计提完再结转：上一根只累计到上一根的 ts，
        # 而 1D 执行周期下这一根内完全可能还跨着一个结算点，不补就永久少记一笔。
        self._funding.accrue_bars(self._curve, from_ts=0, to_ts=int(trade.ts),
                                  mark_px=float(trade.price or 0.0))
        self._leg_fee += float(trade.fee or 0.0)
        source = classify_backtest(str(trade.reason or ""),
                                   stamped=self._pending_exit_source)
        if (self._pending_exit_source is not None
                and is_deterministic_reason(str(trade.reason or ""))):
            self._stamp_conflicts += 1      # R-6：以 reason 为准，但把冲突记下来可观测
        self._pending_exit_source = None
        hold = (None if self._entry_ts is None
                else max(0.0, float(trade.ts) - float(self._entry_ts)))
        # `fee` 传**整笔往返的腿费**（开仓腿 + 平仓腿）：`TradeRecord.fee` 只有平仓腿，而
        # `pnl` 已扣双边——只喂平仓腿会让 `gross = pnl + fee` 少算一条开仓腿。
        record = {"pnl": float(trade.pnl or 0.0), "fee": self._leg_fee}
        self._exits.record(record, hold_seconds=hold, source=source)
        self._accumulate_lifetime(record)
        self._funding.note_close(record, float(trade.ts))
        self._leg_fee = 0.0
        self._entry_ts = None

    def _accumulate_lifetime(self, record: dict) -> None:
        """全程累计成本（[P1-1]；回测无 `AttributionLedger`，故在此按同一条口径累加）。"""
        net = float(record["pnl"])          # `net_mode=True`：SimBroker 的 pnl 已扣双边费
        gross = net + float(record["fee"])
        self._lifetime["trips"] += 1
        self._lifetime["gross"] += gross
        self._lifetime["fee"] += float(record["fee"])
        self._lifetime["net"] += net
        self._lifetime["absGross"] += abs(gross)

    def _lifetime_cost(self) -> dict:
        """与 `AttributionLedger.lifetime_cost()` **逐键同形**（验收卡两世界共用一个 builder）。"""
        life = self._lifetime
        abs_gross = life["absGross"]
        return {"roundTrips": int(life["trips"]), "grossPnlUsd": round(life["gross"], 6),
                "feeUsd": round(life["fee"], 6), "netPnlUsd": round(life["net"], 6),
                "absGrossPnlUsd": round(abs_gross, 6),
                "costRatio": (round(life["fee"] / abs_gross, 4) if abs_gross > 0 else None)}

    def _acceptance_bundle(self) -> dict:
        """回测侧的验收闭环四键（与实盘 `session.acceptance_bundle()` **逐键同形**）。"""
        funding = self._funding.stats(settled_usd=None, unavailable_reason="no_creds")
        inp = build_input(horizon_parts=self._horizon.acceptance_input_parts(),
                          lifetime_cost=self._lifetime_cost(),
                          exit_stats=exit_counters(self._exits), funding_stats=funding,
                          wall_seconds=self._wall_seconds)
        return bundle_from_input(inp, rule=self.horizon_rule, settings=self.settings,
                                 exits=self._exits.stats(), funding=funding)

    def _cost_funding(self, ts: int) -> tuple[FundingSnapshot | None, bool]:
        """回测成本模型的资金费入参 `(snapshot | None, funding_enabled)`（轴 B §2.3.2 **唯一真源**）。

        **曲线不可用 ⇒ 逐字回退 `(None, False)`**（§九 D-8）：`funding_enabled=True` +
        `funding=None` 在 `cost_model` 里的语义是「数据缺失 ⇒ degraded ⇒ 门禁阈值收紧」
        （`cost_model.py` `query_for_preview` 两条分支），那会让「拉不到历史费率」这件观测层的
        小事静默改变回测的门禁判定。回退到 `(None, False)`（有意不建模）才与本轮之前逐字节相同。
        """
        if not self.settings.ai_trade_funding_cost_backtest_enabled:
            return None, False
        snap = self._curve.snapshot_at(int(ts))
        return (snap, True) if snap is not None else (None, False)

    def _horizon_facts(self, i: int, ts: int, ref_px: float) -> HorizonFacts:
        """回测断面（§2.10 对照表）：`now_wall`=当前根 ts、`mark_px`=当根收盘价、`side`=`pos.side`。

        `cost_bps` 走**回测世界**的常数（`backtest_params`）且方向按**持仓**取（[P1-2]/[P1-3]）——
        两侧各自对自己的世界说真话，但前瞻窗口同为 `rule.expected_hold_bars`，否则回测按 8 根算资金费、
        实盘按 12–72 根算，G-3「两侧 reason 逐字相等」不可能通过。
        """
        pos = self.main.pos
        cost = None
        if pos.side in ("long", "short"):
            funding, funding_on = self._cost_funding(ts)      # 轴 B：与实盘同口径的资金费入参
            cost = estimate_round_trip_cost(
                query_for_preview(ord_type="market", side=pos.side,
                                  bar_seconds=BAR_SECONDS[self.bar], funding=funding,
                                  funding_enabled=funding_on),
                backtest_params(self.settings,
                                expected_hold_bars=self._horizon_hold_bars)).total_bps
        return HorizonFacts(
            seq=int(pos.entry_bar), side=pos.side, entry_px=float(pos.entry_price or 0.0),
            mark_px=float(ref_px), leverage=float(self.leverage), now_wall=float(ts),
            bars_in_position=(i - pos.entry_bar) if pos.side != "flat" else None,
            max_hold_bars=self._oco_risk.max_hold_bars,
            stop_loss_pct=self._oco_risk.stop_loss_pct, cost_bps=cost)

    def _profit_guard_wire(self, i: int, ref_px: float) -> dict | None:
        """决策 payload 用的只读保护快照（不推进状态）；未装配 / 空仓 / 无 state ⇒ None ⇒ 整键缺席。

        与实盘的差别只在判定价来源：实盘是 5s 盯市的实时价，回测按根推进只有本根收盘价可用（这条
        差异已写进 §2.3.5 的差异清单，不得让用户以为两者等同）。
        """
        pos = self.main.pos
        if self.profit_rule is None or pos.side == "flat" or self._guard_state is None:
            return None
        facts = PositionFacts(
            seq=pos.entry_bar, side=pos.side, entry_px=float(pos.entry_price),
            leverage=self.leverage, qty_coin=float(pos.qty), mark_px=float(ref_px),
            now_wall=float(self._last_ts), bars_in_position=i - pos.entry_bar,
            atr_px=self._atr_at(i),
            # C5：当前棘轮承诺线进快照（模型每轮看到自己的承诺线 + 有效退出线归因，与实盘 refresh 同款）。
            committed_exit_px=self._committed_exit_px,
            committed_exit_basis=self._committed_exit_basis)
        return build_snapshot(self.profit_rule, self._guard_state, facts)

    def _is_decision_bar(self, i: int, first: int, n: int) -> bool:
        """决策根门控（严格无未来函数）：i<n-1（末根不决策）+ 对齐决策间隔 + 未达轮数硬上限。"""
        if i >= n - 1:                               # 末根无次根可成交（对齐 run_backtest 的 i<n-1）
            return False
        if (i - first) % self.decision_every_bars != 0:
            return False
        if self.rounds_done >= self.max_rounds:      # 到顶：停决策、续盯市至区间末（不静默截断，R10）
            self._reached_cap = True
            return False
        return True

    async def _run_decision(self, job: "_BacktestJob", main_df: pd.DataFrame,
                            context_frames: dict[str, pd.DataFrame], i: int,
                            board: list[StrategySignalView]) -> None:
        """一个决策轮：装配 payload → AI 决策 → 共识门禁 → 映射 Signal→main.submit → 记录/摘要 → 推事件。"""
        round_no = self.rounds_done + 1
        row = main_df.iloc[i]
        ts, ref_px = int(row["ts"]), float(row["c"])
        consensus = compute_consensus(board)
        payload = self._build_payload(
            round_no, ts, ref_px, main_df, context_frames, i, board, consensus)
        decision, decide_degrade = await self.decider.decide(payload)          # 唯一 async（LLM）
        # C5 §2.5：把回测 LLM 本轮承诺的退出线记进 runner 棘轮——第一阶段决策为准（门禁前，同实盘落点）；
        # 快照取自 payload（模型据以决策的那一份），下一根 `_apply_profit_guard_bar` 即据它保守成交序判定。
        self._record_commitment(decision, payload.get("profitGuard"))
        # 【基础策略】铁律门禁（§2.8/P1-4）：与实盘同一纯核心、同一判定价、同一降级码，且同样排在共识
        # 门禁与反思**之前**（被否轮已是 hold ⇒ 下方反思分支天然跳过，零多余 LLM 调用）。
        decision, base_reason = apply_ma_band_gate(
            decision, (payload.get("baseRules") or {}).get("maBand"), self.base_rule,
            last_px=ref_px)
        # ★ 利润保护冷却门禁（F2 §2.3.3a）：与实盘同序（铁律后、共识前）、**同一个纯函数**——严禁在
        # 回测侧复制一份判定（那正是 v1 P1-4 给【基础策略】铁律定下的规矩）。未装配 ⇒ 原样直通（G-3）。
        # ★ 交易周期铁律门禁（holding-horizon §2.10）：与实盘**同一纯核心、同位插入**（基础策略后、
        # 冷却前）。`note_intent` 同样排在一切门禁**之前**（第一阶段意愿口径）。未装配 ⇒ 原样直通。
        self._horizon.note_intent(decision.action, decide_degrade,
                                  position_side=self.main.pos.side, now_wall=float(ts))
        decision, _, _ = self._horizon.gate(decision, None, self._horizon_facts(i, ts, ref_px))
        horizon_reason = self._horizon.last_reason
        decision, cooldown_reason = apply_profit_cooldown(decision, self._cooldown, now_wall=ts)
        # ★ 开仓侧换手纪律（turnover-budget §2.2.5）：与实盘**同一纯核心、同位插入**（冷却后、
        # 刹车前）。`position_side` 取 `self.main.pos.side`，与 `note_intent` 同源。
        decision, turnover_reason = self._horizon.turnover_gate(
            decision, None, position_side=self.main.pos.side, now_wall=float(ts))
        backstop_reason = None   # 轴 B 不在回测装配：回测没有交易所，恒 None（形状仍与实盘同构）
        decision, brake_reason = self._sizing.gate(decision, None, now_wall=float(ts))  # ★ 刹车（第 7 步）
        decision, consensus_reason = apply_consensus_gate(decision, consensus, self.min_consensus)
        # ★ 最小 edge 门禁（§3.7）：与实盘**同一纯核心、同位插入**（共识后、反思前）；k_min=0（默认）
        # ⇒ 恒返回 None ⇒ 既有回测结果逐字节相同。成本用 SimBroker 实收常数（两侧各自对自己的世界说真话）。
        # 轴 B：本轮决策的资金费入参在此**取一次并计数**（`_cost_funding` 在 `_horizon_facts` 里
        # 也会被调一次，但那条只在持仓时走；覆盖率的分母口径是「决策轮」，故计数只挂这一处）。
        edge_funding, edge_funding_on = self._cost_funding(ts)
        self._funding_cost_rounds += 1
        self._funding_cost_covered += 1 if edge_funding_on else 0
        edge_out = evaluate_edge_gate(
            decision, settings=self.settings, last_px=ref_px, atr_px=self._atr_at(i),
            take_profit_pct=self._oco_risk.take_profit_pct, bar_seconds=BAR_SECONDS[self.bar],
            params=backtest_params(self.settings,
                                   expected_hold_bars=self._horizon_hold_bars),
            funding=edge_funding, funding_enabled=edge_funding_on)
        edge_reason = edge_out.reason if edge_out is not None else None
        decision = edge_out.decision if edge_out is not None else decision
        # 八元合成，先执行者胜出：铁律优先级最高，持仓周期次之，再冷却（本系统刚亲手做过的动作，
        # 解释力强于票数），然后换手纪律与兜底覆盖，最后刹车、共识与成本门禁——与实盘
        # `degrade or reason` 的链序逐字同构（[P1-10]：原注释自称「五元」而实为六元，本轮顺手改正
        # 注释，判定式一个字符不动）。
        gate_reason = (base_reason or horizon_reason or cooldown_reason
                       or turnover_reason or backstop_reason
                       or brake_reason or consensus_reason or edge_reason)
        # 二次反思（决策脑不变式：回测复用同一 decider.reflect，emit=None 轨迹全程空转）。verdict 须在分支外
        # 预置 None（RV-3），否则 hold 轮走 else 分支时 _tally_reflection(verdict) 会 NameError。
        verdict = None
        reflect_reason = gate_reason
        # F2（hold-inertia-fix）：apply_reflection 前留存 original 引用（decision 随后可能被改写为 hold）。
        original = decision
        size_before = decision.size_fraction if decision.action in ("open_long", "open_short") else None
        if self.decider.reflection_enabled and decision.action != "hold":
            verdict = await self.decider.reflect(payload, decision)             # emit 缺省 None
            decision, reflect_reason = apply_reflection(
                decision, gate_reason, verdict, self.position_step_pct)         # 复用 session 同款纯函数
        # ★ 仓位求解 + ATR 止损（第 11 步，反思后、_execute 前）。[P0-3] **必须双写**：`apply_risk` 第三形参
        # 根本没被使用、止损价是 `_open` 时由 `self._risk` 冻结进 pos 的，只改 `_oco_risk` 一行都不会生效且
        # 不报错；而它的 take_profit_pct 又被 edge 门禁当上界读（backtest.py:534），故两条必须同步更新。
        decision, plan, _ = self._sizing.resolve(decision, SizingContext(
            ref_px=ref_px, atr_px=self._atr_at(i), step=self.position_step_pct, now_wall=float(ts),
            closes=list(main_df["c"].iloc[:i + 1]), fallback_stop_pct=self._oco_risk.stop_loss_pct,
            notional_full=self.main.equity_at(ref_px) * self.leverage,
            fallback_take_pct=self._oco_risk.take_profit_pct))
        if plan is not None:
            self._oco_risk = replace(self._oco_risk, stop_loss_pct=plan.stop_pct,
                                     take_profit_pct=plan.take_pct)
            self.main.set_risk(self._oco_risk)       # ← 缺这一行 = 彻底空转（simulator.py:123 已存在）
        executed = self._execute(decision)
        self.rounds_done = round_no
        self._tally(decision, decide_degrade, gate_reason)
        self._tally_reflection(verdict, size_before, decision)   # verdict is None → 空转（不递增计数）
        # R-P1-3：commit_round 收门禁后的最终 decision，journal/摘要据此记真实动作（保持上下文纯净）。
        # F2：反思注记随 journal 保真被否决意图（verdict None/未跑 → note=None，零回归）。
        summary = await self.decider.commit_round(
            payload, decision, executed,
            reflection=build_reflection_note(original, verdict, decision))
        self._emit_decision(job, round_no, ts, decision, board, executed, payload,
                            decide_degrade or reflect_reason, verdict)
        if summary is not None:
            self._emit_summary(job, round_no, ts, summary)

    def _execute(self, decision: TradeDecision) -> dict:
        """决策 → Signal（纯核心）→ main.submit（次根开盘成交）；返回本轮执行意图快照。"""
        side_before = self.main.pos.side
        signals = decision_to_signals(decision)
        if signals:
            # 打标槽（轴 B）：运行时**自己**提交的平仓在 submit 之前写槽。反手的平仓腿与主动平仓
            # 是两类退出（前者是换方向、后者是离场），归成一类会让偏置矫正的效果读不出来。
            if decision.action == "close" and side_before in ("long", "short"):
                self._pending_exit_source = "agent"
            elif side_before in ("long", "short") and decision.action.startswith("open_"):
                self._pending_exit_source = "agentReverse"
            self.main.submit(signals)
        return {"ok": True, "action": decision.action, "submitted": bool(signals),
                "positionBefore": side_before,
                "positionAfter": _project_side(side_before, decision.action)}

    def _maybe_session_exit(self, row: pd.Series, i: int, n: int) -> None:
        """会话级 USD 止盈止损：盯市盈亏越阈 → 次根开盘平仓 + 停决策（续盯市至区间末，R11）。"""
        if self._session_exited or i >= n - 1:
            return
        pnl = self.main.equity_at(float(row["c"])) - self.initial_capital
        reason = None
        if self.take_profit_usd is not None and pnl >= self.take_profit_usd:
            reason = "session_take_profit"
        elif self.stop_loss_usd is not None and pnl <= -self.stop_loss_usd:
            reason = "session_stop_loss"
        if reason is not None:
            self.main.submit([Signal(side="close", reason=reason)])
            self._session_exited = True
            self._exit_reason = reason

    # ---------- payload / 统计 / 事件 ----------
    def _build_payload(self, round_no: int, ts: int, ref_px: float, main_df: pd.DataFrame,
                       context_frames: dict[str, pd.DataFrame], i: int,
                       board: list[StrategySignalView], consensus: Consensus) -> dict:
        session_pnl = self.main.equity_at(ref_px) - self.initial_capital
        payload = {
            "round": round_no, "barTs": ts, "lastPx": ref_px,
            "position": self._position_payload(ref_px),
            "sessionPnlUsd": round(session_pnl, 4), "budgetUsd": self.initial_capital,
            "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
            "marketContext": build_market_context(main_df.iloc[:i + 1], self.bar, self.settings),
            "marketDigest": build_market_digest(         # R1：因果切片 iloc[:i+1]，末行=当前决策根（无未来函数）
                main_df.iloc[:i + 1], self.bar, self.settings),
            "multiTimeframeContext": self._build_multi_timeframe_context(
                context_frames, ts, ref_px),
            "consensus": _consensus_wire(consensus),
            "signalBoard": [_view_full(v) for v in board]}
        # 【基础策略】通道快照：未装配 ⇒ **整键缺席**（prompt 逐字节回退）；band 取回测帧到当前根的
        # 因果切片 iloc[:i+1]，与 marketContext/marketDigest 同一取数姿态（绝不引入未来根）。
        if self.base_rule is not None:
            payload["baseRules"] = {
                "maBand": build_ma_band(main_df.iloc[:i + 1], self.base_rule)}
        # 利润保护快照 + 冷却视图（F3 §2.3.4）：未装配 / 空仓 / 冷却未激活 ⇒ **整键缺席** ⇒
        # `prompts.py` 的既有渲染管线整段省略 ⇒ prompt 模板零改动、逐字节回退。
        guard = self._profit_guard_wire(i, ref_px)
        if guard is not None:
            payload["profitGuard"] = guard
        cooldown = build_cooldown_view(self._cooldown, now_wall=ts)
        if cooldown is not None:
            payload["profitGuardCooldown"] = cooldown
        # 交易周期段（§2.10）：同款「整键缺席」范式；须在 profitGuard 之后——它顺带给保护快照打
        # `horizonArmed` 标记（§2.9 a/b 的两处抢跑文案据它切换，两侧同源）。
        self._horizon.inject(payload, self._horizon.refresh(self._horizon_facts(i, ts, ref_px)))
        return payload

    def _build_multi_timeframe_context(
        self,
        context_frames: dict[str, pd.DataFrame],
        ts: int,
        ref_px: float,
    ) -> dict | None:
        try:
            return build_multi_timeframe_context(
                execution_bar=self.bar,
                frames=context_frames,
                as_of_ts=ts + BAR_SECONDS[self.bar],
                last_px=ref_px,
                settings=self.settings,
            )
        except ValueError as exc:
            logger.warning(
                "invalid backtest multi-timeframe contract: bar=%s ts=%s price=%s (%s)",
                self.bar, ts, ref_px, exc,
            )
            return None

    def _position_payload(self, ref_px: float) -> dict | None:
        """主账户持仓视图（盯市未实现盈亏用 `equity_at-cash`，R7：context() 无未实现盈亏字段）。"""
        ctx = self.main.context()
        if ctx.position_side == "flat":
            return None
        unrealized = self.main.equity_at(ref_px) - self.main.cash
        return {"side": ctx.position_side, "avgEntry": round(ctx.entry_price, 6),
                "size": round(self.main.pos.qty, 8), "unrealizedPnl": round(unrealized, 4),
                "barsInPosition": ctx.bars_in_position}

    def _tally(self, decision: TradeDecision, decide_degrade: str | None,
               gate_reason: str | None) -> None:
        self.action_counts[decision.action] = self.action_counts.get(decision.action, 0) + 1
        if decision.confidence is not None:
            self._confidences.append(decision.confidence)
        if gate_reason is not None:                  # 共识门禁把 open_* 降级为 hold 的轮数
            self.degraded_rounds += 1
        if decide_degrade in ("decision_timeout", "llm_error"):   # LLM 超时/失败回落 hold 的轮数
            self.timeout_rounds += 1

    def _tally_reflection(self, verdict, size_before: float | None,
                          decision_after: TradeDecision) -> None:
        """反思观测计数（§2.5）：`verdict is None`（关闭/hold 轮）→ 空转，保证 reflection_enabled=False
        逐字等价旧行为。否则计 runs，并据裁决计 aborts（→hold）或 adjustments（confirm 且严格收缩）。"""
        if verdict is None or not verdict.ran:
            return
        self.reflection_runs += 1
        if verdict.degrade_reason is not None or verdict.verdict == "abort":
            self.reflection_aborts += 1
        elif (size_before is not None and decision_after.size_fraction is not None
              and decision_after.size_fraction < size_before):
            self.reflection_adjustments += 1

    def _emit_decision(self, job: "_BacktestJob", round_no: int, ts: int, decision: TradeDecision,
                       board: list[StrategySignalView], executed: dict, payload: dict,
                       degrade: str | None, verdict=None) -> None:
        data = {"ts": ts, "round": round_no, "barIndex": self._bars_processed,
                "action": decision.action, "sizeFraction": decision.size_fraction,
                "confidence": decision.confidence, "reason": decision.reason,
                "signalBoard": [_view_slim(v) for v in board],
                "marketContext": payload.get("marketContext"), "consensus": payload.get("consensus"),
                "marketDigest": payload.get("marketDigest"),   # RC-10：与实盘 decision 事件对称
                "multiTimeframeContext": payload.get("multiTimeframeContext"),
                "degradeReason": degrade, "executed": executed,
                "reflectionVerdict": verdict.verdict if verdict is not None and verdict.ran else None}
        self._decisions_digest.append(data)
        job.publish({"type": "decision", "data": data})

    def _emit_summary(self, job: "_BacktestJob", round_no: int, ts: int, summary: str) -> None:
        self.summary_count += 1
        record = {"round": round_no, "ts": ts, "summary": summary}
        self._summaries_all.append(record)
        job.publish({"type": "summary", "data": record})

    def _emit_new_trades(self, job: "_BacktestJob") -> None:
        while self._trades_emitted < len(self.main.trades):
            trade = self.main.trades[self._trades_emitted]
            self._trades_emitted += 1
            if trade.side == "close":       # 回测侧成交唯一收口 ⇒ 连亏计数（与实盘 _dispatch 同位）
                self._sizing.note_close(trade.pnl, now_wall=float(trade.ts))
            job.publish({"type": "trade", "data": _trade_wire(trade)})

    def _emit_progress(self, job: "_BacktestJob", i: int, first: int, total: int,
                       row: pd.Series) -> None:
        if self._bars_processed % _PROGRESS_EVERY_BARS != 0 and i != first + total - 1:
            return
        equity = self.main.equity_at(float(row["c"]))
        job.publish({"type": "progress", "data": {
            "barIndex": self._bars_processed, "totalBars": total, "ts": int(row["ts"]),
            "lastPx": float(row["c"]), "pct": round(self._bars_processed / total, 4),
            "decisionRound": self.rounds_done, "plannedRounds": self.planned_rounds,
            "equity": round(equity, 4), "positionSide": self.main.pos.side,
            "sessionPnlUsd": round(equity - self.initial_capital, 4)}})

    # ---------- finalize + 落库 ----------
    async def _finalize(self, job: "_BacktestJob", main_df: pd.DataFrame, first: int,
                        state: str) -> None:
        if self._last_ts:
            self.main.finalize(ts=self._last_ts, close=self._last_close)
            self._emit_new_trades(job)               # 补发收尾平仓成交
        metrics = compute_metrics(self.main.equity_points, self.main.trades, self.bar,
                                  self.initial_capital)
        ai_stats = self._aggregate_stats()
        ext = self._extended_metrics(job)                     # [B1] 扩展绩效指标（派生失败回落 {}）
        bench = self._buy_hold_benchmark(job, main_df, first, metrics)  # [B2] 买入持有基准 + alpha
        notes = self._build_notes(main_df, first)
        result = self._build_result(metrics, ai_stats, notes)
        result["extendedMetrics"] = ext                       # 追加两键（旧版结果为 {}，向后兼容）
        result["benchmark"] = bench
        run_id = await self._persist(metrics, ai_stats, state, ext, bench)
        result["runId"] = run_id
        job.publish({"type": "result", "data": result})
        terminal = "cancelled" if state == "cancelled" else "completed"
        job.mark_terminal(terminal, {"jobId": job.job_id, "runId": run_id})

    def _extended_metrics(self, job: "_BacktestJob") -> dict:
        """B1 扩展绩效指标（派生失败记 warning 回落空 dict，绝不影响主 result/落库，CLAUDE.md §5）。"""
        try:
            return compute_extended_metrics(
                self.main.equity_points, self.main.trades, self.bar,
                self.initial_capital, bars_in_market=self._bars_in_market)
        except Exception as exc:  # noqa: BLE001 —— 分析层异常不得静默吞、不得波及主结果
            logger.warning("ai-backtest extended-metrics failed (job %s): %s", job.job_id, exc)
            return {}

    def _buy_hold_benchmark(self, job: "_BacktestJob", main_df: pd.DataFrame, first: int,
                            metrics: dict) -> dict:
        """B2 买入持有基准 + 超额 alpha（同窗口 iloc[first:]；派生失败记 warning 回落空 dict）。"""
        try:
            closes = main_df["c"].iloc[first:].astype(float).tolist()
            stamps = main_df["ts"].iloc[first:].astype(int).tolist()
            bench = compute_buy_hold(closes, stamps, self.initial_capital, self.bar)
            if bench:
                bench["alpha"] = round(metrics["totalReturn"] - bench["totalReturn"], 6)
            return bench
        except Exception as exc:  # noqa: BLE001 —— 同上：warning + 回落，不阻断
            logger.warning("ai-backtest benchmark failed (job %s): %s", job.job_id, exc)
            return {}

    def _aggregate_stats(self) -> dict:
        total = self.rounds_done
        confidences = self._confidences
        return {
            "decisionRounds": total, "actionCounts": dict(self.action_counts),
            "holdRate": round(self.action_counts["hold"] / total, 4) if total else 0.0,
            "avgConfidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
            "degradedRounds": self.degraded_rounds, "timeoutRounds": self.timeout_rounds,
            "reflectionRuns": self.reflection_runs, "reflectionAborts": self.reflection_aborts,
            "reflectionAdjustments": self.reflection_adjustments,
            "summaryCount": self.summary_count, "finalSummary": self.decider.summary,
            "decisionEveryBars": self.decision_every_bars, "plannedRounds": self.planned_rounds,
            "reachedRoundCap": self._reached_cap,
            # F3：与实盘 aiStats 同名同义（事件级触发次数）；未装配的回测恒 0。
            "profitGuardExits": self.profit_guard_exits,
            # 本次回测**是否真的装配了保护层**（RV-1）：`profitGuardExits` 恒下发且未装配时也是 0，
            # 只凭它无法把「装了但没触发」与「压根没装」分开——UI 据此才不会在一次没有保护层的回测上
            # 挂出「利润保护（保守近似）」的卡片，宣称一条并不存在的防线（v1 终审 RV-1 的同一条教训）。
            "profitGuardEnabled": self.profit_rule is not None,
            # 交易周期层观测（§2.12）：与实盘 `aiStats.horizon` **同名同形**（同一个 `stats()`），
            # 未装配 ⇒ `None` ⇒ UI 整段不渲染（绝不在一次没有周期契约的回测上挂出该卡片）。
            "horizon": self._horizon.stats(),
            # 验收闭环四键（horizon-acceptance）：与实盘 `_finalize_run` 的四键**逐键同形**
            # （同一个 builder、同一份 wire），这是回测 vs 实盘可对拍的前提。
            **ai_stats_keys(self._acceptance_bundle())}

    def _build_notes(self, main_df: pd.DataFrame, first: int) -> list[str]:
        notes: list[str] = []
        if self._reached_cap:
            notes.append(f"已达决策上限（{self.max_rounds} 轮），末段仅逐根盯市至区间末，未再决策。")
        if self._session_exited:
            label = "止盈" if self._exit_reason == "session_take_profit" else "止损"
            notes.append(f"已触发会话级{label}，触发后次根开盘平仓，末段仅盯市至区间末。")
        if 0 <= first < len(main_df) and int(main_df.iloc[first]["ts"]) > self.start_ts:
            notes.append("历史深度或暖机不足，首个决策晚于所选区间起点，起始若干根未参与决策。")
        if self.profit_rule is not None:
            notes.append(_PROFIT_GUARD_BACKTEST_NOTE)
        if self.settings.ai_trade_edge_gate_k_min > 0:
            notes.append(_EDGE_GATE_BACKTEST_NOTE)
        if self._sizing.armed:
            notes.append(SIZING_BACKTEST_NOTE)
        # 分辨率免责（§2.10 / [P2-7]）：**点名具体模式**——`hourly_swing`（5m 执行 + 180s 窗口）在
        # 回测里 G1 恒不生效，不写清楚用户仍会拿这份回测去为实盘的最短持仓窗口背书。
        notes.extend(format_backtest_notes(self.horizon_rule, BAR_SECONDS[self.bar],
                                           turnover=self._horizon.turnover_rule))
        notes.extend(self._funding_notes())
        return notes

    def _funding_notes(self) -> list[str]:
        """资金费口径免责（horizon-acceptance §2.2.4 第 4、5 点 + 本轮轴 B §2.4.4）。

        [P1-6] **成本估算口径句恒在最前、且不受 `_funding.enabled` 早退连坐**：本函数是资金费口径
        的唯一披露出口，而成本估算的口径变化与「记账是否开启」「有没有装配周期契约」都无关——
        `_horizon_facts` 与 `evaluate_edge_gate` 在两种臂上都会跑，对照臂恰恰是验收时最该对拍的
        那条曲线（挂在 `format_backtest_notes` 上会因 `rule is None ⇒ []` 而整句消失）。
        """
        notes: list[str] = [self._cost_basis_note()]
        if not self._funding.enabled:
            return notes
        if not self._curve.available:
            notes.append("本次回测未能取到历史资金费曲线，净额**未含**资金费，"
                         "验收卡「资金费后净收益」一行按「口径不可得」呈现（绝不按 0 计）。")
            return notes
        notes.append("资金费按交易所历史费率曲线逐结算点计提，但**未计入权益曲线与 metrics**，"
                     "仅在周期验收卡以「资金费后净收益」单列——"
                     "`SimBroker` 被全仓库所有回测共用，在其中扣资金费会改变每一个既有策略回测的结果。")
        if self._stamp_conflicts:
            notes.append(f"退出来源归因中有 {self._stamp_conflicts} 笔的运行时打标与成交理由冲突，"
                         "已一律以成交理由为准。")
        return notes

    def _cost_basis_note(self) -> str:
        """本次回测的**往返成本估算**到底含不含资金费（轴 B §2.4.4，取代旧的欠账披露句）。

        旧那句「已知欠账：成本估算仍不含资金费」在轴 B 落地后是一句假话，必须由本函数取代——
        **一条在事实变化后没被撤下的免责，比没有免责更坏**（[P1-6] / R-14）。
        括号里的「与最小 edge 门禁」是条件文本：`k_min` 默认 0 时 `evaluate_edge_gate` 第一句即
        `return None`，无条件写上就又是一句没核实过的承诺（[P1-9]，沿用 `_build_notes` 的同款先例）。
        """
        gate = "与最小 edge 门禁" if self.settings.ai_trade_edge_gate_k_min > 0 else ""
        if self._funding_cost_covered <= 0:
            # 三种「不含」必须分开说：总闸关是运维选择、曲线不可得是观测层失败、而「曲线在手但本次
            # 区间的决策轮全落在它起点之前」是第三件事——把它说成「曲线不可得」正是 [P2-1] 要根除的
            # 那种「一个全局布尔说不清」的口径。
            why = ("运维总闸 `ai_trade_funding_cost_backtest_enabled` 已关闭"
                   if not self.settings.ai_trade_funding_cost_backtest_enabled
                   else "历史资金费曲线不可得" if not self._curve.available
                   else "本次区间没有任何决策轮读到可见的历史结算点")
            return (f"本次回测的往返成本估算**不含**资金费（{why}），G3 换手迟滞{gate}的判定因此比"
                    "实盘宽松；持仓越长偏差越大，不得据此为长周期模式背书。")
        interval_h = float(self._curve.interval_hint or 0.0) / 3600.0
        periods = funding_periods(
            query_for_preview(ord_type="market", side="long",
                              bar_seconds=BAR_SECONDS[self.bar],
                              funding=self._curve.snapshot_at(self._last_ts or self.end_ts),
                              funding_enabled=True),
            backtest_params(self.settings, expected_hold_bars=self._horizon_hold_bars))
        return (f"本次回测的往返成本估算已含资金费（真实历史曲线，结算周期约 {interval_h:g}h，"
                f"按目标持仓跨 {periods} 期，覆盖 {self._funding_cost_covered}/"
                f"{self._funding_cost_rounds} 个决策轮）；G3 换手迟滞{gate}据此判定。")

    def _build_result(self, metrics: dict, ai_stats: dict, notes: list[str]) -> dict:
        equity = [{"ts": e.ts, "equity": round(e.equity, 4), "drawdown": round(e.drawdown, 6)}
                  for e in self.main.equity_points]
        return {
            "runId": None, "metrics": metrics, "aiStats": ai_stats, "equity": equity,
            "trades": [_trade_wire(t) for t in self.main.trades],
            "decisionsDigest": list(self._decisions_digest), "summaries": list(self._summaries_all),
            "startTs": self.start_ts, "endTs": self.end_ts, "barsProcessed": self._bars_processed,
            "notes": notes}

    async def _persist(self, metrics: dict, ai_stats: dict, state: str,
                       ext: dict, bench: dict) -> int | None:
        """落 Run（mode=ai-backtest，strategy_id 用主策略占位）+ trades + equity（尽力而为，失败不崩）。"""
        status = {"completed": "completed", "cancelled": "cancelled"}.get(state, "failed")
        try:
            async with get_sessionmaker()() as session:
                run = await repo.create_run(
                    session, strategy_id=self.strategy_ids[0], inst_id=self.inst_id, bar=self.bar,
                    mode="ai-backtest", config=self._run_config(), status="running")
                metrics_full = {**metrics, "aiStats": ai_stats,
                                "finalSummary": self.decider.summary,
                                "decisionsDigest": list(self._decisions_digest),
                                "extendedMetrics": ext, "benchmark": bench}
                await repo.persist_backtest_result(
                    session, run, trades=self.main.trades, equity=self.main.equity_points,
                    metrics=metrics_full, start_ts=self.start_ts, end_ts=self.end_ts, status=status)
                return run.id
        except Exception as exc:  # noqa: BLE001 —— 落库失败不阻塞终态：result 事件已含全量，前端可渲染
            logger.warning("ai-backtest persist failed (job strategies=%s): %s",
                           self.strategy_ids, exc)
            return None

    def _run_config(self) -> dict:
        return {"strategyIds": self.strategy_ids, "strategyNames": self.strategy_names,
                "startTs": self.start_ts, "endTs": self.end_ts,
                "decisionEveryBars": self.decision_every_bars,
                "contextRefreshRounds": self.context_refresh_rounds,
                "initialCapital": self.initial_capital, "leverage": self.leverage,
                "stopLossPct": self._oco_risk.stop_loss_pct,
                "takeProfitPct": self._oco_risk.take_profit_pct,
                "takeProfitUsd": self.take_profit_usd, "stopLossUsd": self.stop_loss_usd,
                "minConsensus": self.min_consensus,
                "positionStepPct": self.position_step_pct,
                # P2-6：回填本次保护参数，否则 run 的 config_json 无法复现这次回测（与 §4.1 的
                # 「回执与 config_json 由 rule_wire 镜像全部字段」承诺矛盾）。未装配 → None。
                "profitGuard": rule_wire(self.profit_rule),
                # 同上：不回填本次周期契约，run 的 config_json 就复现不了这次回测（含两个派生量）。
                "tradingHorizon": horizon_rule_wire(self.horizon_rule)}


def _compute_warmup(specs: list[PaperStrategySpec], regime_enabled: bool) -> int:
    """暖机根数：策略暖机 / 高周期暖机 / 体制最小根数 的最大值（≥1），使用户区间首根即可决策。"""
    base = max((s.strategy.warmup() for s in specs), default=1)
    htf = max((s.strategy.htf_warmup_bars() for s in specs), default=0)
    regime = _REGIME_MIN_BARS if regime_enabled else 0
    return max(base, htf, regime, 1)


def _http_detail(exc: HTTPException) -> tuple[str, str]:
    """从 HTTPException.detail 安全提取 (code, message)（detail 可为统一错误 dict 或裸 str）。"""
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("code", "AI_BACKTEST_FAILED")), str(detail.get("message", ""))
    return "AI_BACKTEST_FAILED", str(detail)


class _BacktestJob:
    """单个流式 AI 回测作业：有界环形缓冲 + 订阅扇出 + 协作式取消 + 终态 state（镜像 `_TrainJob` 分层）。"""

    def __init__(self, job_id: str, runner: AiBacktestRunner):
        self.job_id = job_id
        self.runner = runner
        self.state = "running"                       # running|completed|cancelled|failed
        self.phase = "loading"
        self.progress = 0.0
        self.bar_index = 0
        self.total_bars = 0
        self.decision_round = 0
        self.planned_rounds = runner.planned_rounds
        self.started_ts = int(time.time())
        self.run_id: int | None = None
        self.result: dict | None = None
        self.finished_mono: float | None = None
        self._decisions: deque[dict] = deque(maxlen=_DECISIONS_BUFFER_MAX)
        self._summaries: deque[dict] = deque(maxlen=_SUMMARIES_BUFFER_MAX)
        self._equity: deque[dict] = deque(maxlen=_EQUITY_BUFFER_MAX)
        self._terminal_event: dict | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._cancel = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ---------- fan-out ----------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        """更新可查询快照字段（有界保留）+ 扇出给订阅者（满队列丢弃，重连经 snapshot 回放）。"""
        self._retain(event)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _retain(self, event: dict) -> None:
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "phase":
            self.phase = data.get("phase", self.phase)
        elif etype == "progress":
            self._retain_progress(data)
        elif etype == "decision":
            self._decisions.append(data)
        elif etype == "summary":
            self._summaries.append(data)
        elif etype == "result":
            self.result = data
            self.run_id = data.get("runId")
        elif etype in _TERMINAL_TYPES:
            self._terminal_event = event

    def _retain_progress(self, data: dict) -> None:
        self.progress = data.get("pct", self.progress)
        self.bar_index = data.get("barIndex", self.bar_index)
        self.total_bars = data.get("totalBars", self.total_bars)
        self.decision_round = data.get("decisionRound", self.decision_round)
        if data.get("equity") is not None:
            self._equity.append({"ts": data.get("ts"), "equity": data.get("equity")})

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self._task = asyncio.create_task(self.runner.run(self))

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def is_terminal(self) -> bool:
        return self.state in ("completed", "cancelled", "failed")

    def request_cancel(self) -> None:
        if not self.is_terminal:
            self._cancel.set()

    def mark_terminal(self, state: str, data: dict) -> None:
        """置终态并推终态事件（幂等）：completed→done / cancelled→cancelled / failed→error。"""
        if self.is_terminal:
            return
        self.state = state
        self.finished_mono = time.monotonic()
        etype = {"completed": "done", "cancelled": "cancelled", "failed": "error"}[state]
        self.publish({"type": etype, "data": data})

    async def join(self) -> None:
        task = self._task
        if task is not None and not task.done():
            try:
                await task
            except asyncio.CancelledError:
                pass

    def force_cancel(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()

    # ---------- 只读快照 ----------
    def status_dict(self) -> dict:
        return {"jobId": self.job_id, "state": self.state, "phase": self.phase,
                "progress": self.progress, "barIndex": self.bar_index, "totalBars": self.total_bars,
                "decisionRound": self.decision_round, "plannedRounds": self.planned_rounds,
                "instId": self.runner.inst_id, "bar": self.runner.bar,
                "strategyIds": self.runner.strategy_ids, "startTs": self.runner.start_ts,
                "endTs": self.runner.end_ts, "startedTs": self.started_ts,
                "runId": self.run_id, "result": self.result}

    def snapshot_events(self) -> list[dict]:
        """WS 连接即原子回放：snapshot（含 job/决策/摘要/权益近端）+ 若已终态则 result + 终态事件。"""
        events: list[dict] = [{"type": "snapshot", "data": {
            "job": self.status_dict(), "decisions": list(self._decisions),
            "summaries": list(self._summaries), "equity": list(self._equity)}}]
        if self.is_terminal:
            if self.result is not None:
                events.append({"type": "result", "data": self.result})
            if self._terminal_event is not None:
                events.append(self._terminal_event)
        return events
