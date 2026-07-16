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
from dataclasses import dataclass

import pandas as pd
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool

from ..agent.llm.factory import make_llm_client_from_config
from ..config import Settings
from ..db import repositories as repo
from ..db.session import get_sessionmaker
from ..engine.backtester import candles_to_df
from ..engine.benchmark import compute_buy_hold
from ..engine.metrics import compute_metrics
from ..engine.metrics_ext import compute_extended_metrics
from ..engine.mtf import bar_to_seconds
from ..engine.simulator import RiskParams, SimBroker, TradeRecord
from ..okx.types import BAR_SECONDS
from ..paper.strategy_factory import PaperStrategySpec
from ..strategies.base import Signal
from ..agent.services import load_htf_frames, load_range_candles
from .backtest_frames import build_backtest_frames
from .decider import AiTradeDecider, TradeDecision
from .market_context import build_market_context
from .market_digest import build_market_digest
from .multi_timeframe_context import (
    build_multi_timeframe_context,
    decision_timeframes,
)
from .prompts import build_ai_trade_system_prompt
from .reflection import apply_reflection
from .signals import (
    Consensus,
    StrategySignalView,
    apply_consensus_gate,
    compute_consensus,
    decision_to_signals,
    evaluate_signal_board,
    make_shadow_broker,
)

logger = logging.getLogger(__name__)

_REGIME_MIN_BARS = 60          # engine/regime 单点体制判定最小根数（暖机须覆盖）
_YIELD_EVERY_BARS = 16         # 逐根循环每 N 根 await sleep(0) 让出事件循环（R4）
_PROGRESS_EVERY_BARS = 2       # 进度事件节流：每 N 根推一次（外加末根必推）
_DECISIONS_BUFFER_MAX = 200    # 决策环形缓冲上限（重连仅回放近端，全量走落库/result）
_SUMMARIES_BUFFER_MAX = 64     # 摘要缓冲上限
_EQUITY_BUFFER_MAX = 1000      # 权益缓冲上限（滚动迷你图重连回放）
_TERMINAL_TYPES = ("done", "cancelled", "error")


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

        self.warmup = _compute_warmup(self.specs, spec.settings.ai_trade_regime_context)
        # allow_limit 恒 False（ai-trade-limit-orders §2.10，Non-goal）：`SimBroker._pending` 是「次根
        # 开盘无条件成交」的一根队列、`_Order` 连价格字段都没有。放任 Agent 在回测里挂限价而每单必成，
        # 会**系统性高估**限价策略——这比「回测不支持限价」严重得多。故回测里 Agent 根本看不到
        # orderType/limitPrice，只能市价：零分歧、诚实。此处显式写出（而非依赖默认值）以表明是决策。
        self.decider = AiTradeDecider(
            make_llm_client_from_config(spec.provider),
            build_ai_trade_system_prompt(self.inst_id, self.bar, self.initial_capital, self.leverage,
                                         position_step_pct=self.position_step_pct,
                                         allow_limit=False),
            context_refresh_rounds=self.context_refresh_rounds, settings=self.settings,
            position_step_pct=self.position_step_pct, reflection_enabled=self.reflection_enabled,
            allow_limit=False)
        self._oco_risk = RiskParams(
            stop_loss_pct=spec.stop_loss_pct or None, take_profit_pct=spec.take_profit_pct or None,
            max_hold_bars=self.settings.ai_trade_default_max_hold_bars or None)
        self.main = SimBroker(self.initial_capital,
                              taker_fee_bps=self.settings.default_taker_fee_bps,
                              slippage_bps=self.settings.default_slippage_bps, leverage=self.leverage)
        self.main.set_risk(self._oco_risk)
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
        job.publish({"type": "phase", "data": {"phase": "priming"}})
        await run_in_threadpool(self._prime_shadows, dfs, first)
        if job.cancelled:
            return None
        return main_df, dfs, context_frames, first

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
            self.main.mark(ts=int(row["ts"]), close=float(row["c"]))
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
        decision, gate_reason = apply_consensus_gate(decision, consensus, self.min_consensus)
        # 二次反思（决策脑不变式：回测复用同一 decider.reflect，emit=None 轨迹全程空转）。verdict 须在分支外
        # 预置 None（RV-3），否则 hold 轮走 else 分支时 _tally_reflection(verdict) 会 NameError。
        verdict = None
        reflect_reason = gate_reason
        size_before = decision.size_fraction if decision.action in ("open_long", "open_short") else None
        if self.decider.reflection_enabled and decision.action != "hold":
            verdict = await self.decider.reflect(payload, decision)             # emit 缺省 None
            decision, reflect_reason = apply_reflection(
                decision, gate_reason, verdict, self.position_step_pct)         # 复用 session 同款纯函数
        executed = self._execute(decision)
        self.rounds_done = round_no
        self._tally(decision, decide_degrade, gate_reason)
        self._tally_reflection(verdict, size_before, decision)   # verdict is None → 空转（不递增计数）
        # R-P1-3：commit_round 收门禁后的最终 decision，journal/摘要据此记真实动作（保持上下文纯净）。
        summary = await self.decider.commit_round(payload, decision, executed)
        self._emit_decision(job, round_no, ts, decision, board, executed, payload,
                            decide_degrade or reflect_reason, verdict)
        if summary is not None:
            self._emit_summary(job, round_no, ts, summary)

    def _execute(self, decision: TradeDecision) -> dict:
        """决策 → Signal（纯核心）→ main.submit（次根开盘成交）；返回本轮执行意图快照。"""
        side_before = self.main.pos.side
        signals = decision_to_signals(decision)
        if signals:
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
        return {
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
            "reachedRoundCap": self._reached_cap}

    def _build_notes(self, main_df: pd.DataFrame, first: int) -> list[str]:
        notes: list[str] = []
        if self._reached_cap:
            notes.append(f"已达决策上限（{self.max_rounds} 轮），末段仅逐根盯市至区间末，未再决策。")
        if self._session_exited:
            label = "止盈" if self._exit_reason == "session_take_profit" else "止损"
            notes.append(f"已触发会话级{label}，触发后次根开盘平仓，末段仅盯市至区间末。")
        if 0 <= first < len(main_df) and int(main_df.iloc[first]["ts"]) > self.start_ts:
            notes.append("历史深度或暖机不足，首个决策晚于所选区间起点，起始若干根未参与决策。")
        return notes

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
                "positionStepPct": self.position_step_pct}


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
