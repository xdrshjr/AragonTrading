"""AI 自动交易「决策认知轨迹」纯逻辑（ai-auto-trading-agent-trace §3.5）。

集中承载轨迹相关的无副作用装配与流式合并，避免 `decider.py`/`session.py` 膨胀：
- `_TraceCoalescer`：把决策器高频 `text_delta`/`thinking_delta` 按 phase 合并后再 `emit`，
  避免撑爆 WS 队列（`_push` 满队列会丢帧）；`emit is None`（回测 / 单测无订阅）时全程空转，
  决策行为与今日逐字一致。
- `new_round_trace`：装配「在飞轮次」初值（thinking/analysis 空串、state="deciding"）。
- `trace_event`：构造 `agent_trace` WS 事件信封 `{type,data:{phase,round,ts,**data}}`。

阈值为模块级常量（与 `_DECISIONS_MAX`/`_SNAPSHOT_EQUITY_MAX` 同风格，规避「AI_TRADE_ 前缀 env
三处联动」的既知易踩点，§八）。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # 仅类型标注用，运行期不导入（with future annotations 不触发环）
    from .decider import ReflectionVerdict, TradeDecision

# 轨迹阶段常量（前后端镜像；session 按此织入，前端 reducer 按此归约）。
# retry：可重试失败降级前的重试面包屑（ai-trade-decision-llm-retry §4.4），前端据此重置本轮累积。
# reflection_*：下单前二次反思三阶段（ai-trade-pre-trade-reflection §2.6），仅交易意愿轮出现。
# base_rule：【基础策略】铁律门禁段（ai-trade-base-strategy-ma-band §2.9），**仅在规则装配时**每轮一条。
# edge_gate：最小 edge 门禁段（ai-trade-cost-accounting-and-edge-gate §5.3），**仅 k_min>0 时**每轮一条。
# sizing：仓位求解 + ATR 止损 + 刹车段（vol-sizing-and-drawdown-brake §3.9），**仅规则装配时**每轮一条。
# 之所以值得新增一个相位（代价 = 8 个前端镜像点）：求解发生在**轮次末尾**（管线第 11 步），`round_start`
# 早已发出、搭不上；且本层新增了 5 个「看起来生效但可能没生效」的观测量（scaleErased / stopSource 的三个
# skip 态 / exposureCapUnreachable / volSource），它们必须每轮可见——静默的保守是本仓库的前科。
PHASES = ("round_start", "thinking", "analysis", "tool_call_start",
          "tool_call", "result", "summary", "error", "retry",
          "reflection_start", "reflection", "reflection_verdict", "base_rule", "edge_gate",
          "sizing")

# `sizing` 事件与在飞轮次共用的键集合（**单一真源**：事件装配、轮次初值、前端镜像三处照它对齐）。
SIZING_TRACE_KEYS = (
    "brakeLevel", "brakeReason", "drawdownPct", "lossStreak", "peakEquityBasis",
    "unrealizedShare", "modelFraction", "fVol", "fKelly", "fCap", "kellyStatus",
    "fFinalRaw", "finalFraction", "scaleSource", "scaleErased", "volSource",
    "stopPct", "takePct", "stopSource", "effectiveExposure", "effectiveExposureCap",
    "corrScale", "exposureCapUnreachable")

# 合并冲刷阈值：累计 ≥48 字符 / 相变 / 工具起始 / 收尾 即冲刷一条 delta（轮间隔以分钟计，量级极低）。
_TRACE_FLUSH_CHARS = 48
# 整轮单 phase 累计文本硬上限（防滥用），超出后丢弃尾部。
_TRACE_MAX_CHARS = 4000
# 轮次轨迹环形容量（仅内存 / 快照回放，进程内随会话结束回收）。
_TRACES_MAX = 20

# 由 session 注入的 push 闭包：接收 `{phase, delta}` 或 `{phase:tool_call_start, name}`，补 round/ts 后扇出。
TraceEmit = Callable[[dict], Awaitable[None]]


class _TraceCoalescer:
    """按 phase 累积决策器流式增量，达阈值 / 相变 / 收尾时冲刷一条合并 delta（`emit is None` 空转）。"""

    def __init__(self, emit: TraceEmit | None, *,
                 flush_chars: int = _TRACE_FLUSH_CHARS,
                 max_chars: int = _TRACE_MAX_CHARS):
        self._emit = emit
        self._flush_chars = flush_chars
        self._max_chars = max_chars
        self._phase: str | None = None      # 当前缓冲的 phase（thinking / analysis）
        self._buf: list[str] = []
        self._buf_len = 0
        self._sent: dict[str, int] = {}     # 各 phase 已发字符数（供 max 截断）

    async def feed(self, phase: str, text: str) -> None:
        """喂入某 phase 的增量；phase 切换先冲刷旧段，超单 phase 硬上限则丢弃尾部。"""
        if self._emit is None or not text:
            return
        if self._phase is not None and phase != self._phase:
            await self.flush()
        self._phase = phase
        remaining = self._max_chars - self._sent.get(phase, 0) - self._buf_len
        if remaining <= 0:
            return
        chunk = text if len(text) <= remaining else text[:remaining]
        self._buf.append(chunk)
        self._buf_len += len(chunk)
        if self._buf_len >= self._flush_chars:
            await self.flush()

    async def flush(self) -> None:
        """把当前缓冲作为一条 `{phase, delta}` 发出（空缓冲则仅复位）。"""
        if self._emit is None or not self._buf or self._phase is None:
            self._buf = []
            self._buf_len = 0
            return
        delta = "".join(self._buf)
        phase = self._phase
        self._buf = []
        self._buf_len = 0
        self._sent[phase] = self._sent.get(phase, 0) + len(delta)
        await self._emit({"phase": phase, "delta": delta})

    async def mark(self, name: str) -> None:
        """模型开始调用工具：先由调用方 flush 文本段，再发一条 tool_call_start 占位。"""
        if self._emit is None:
            return
        await self._emit({"phase": "tool_call_start", "name": name})


def new_round_trace(round_no: int, ts: int, ref_px: float,
                    payload: dict, signal_board: list[dict]) -> dict:
    """装配「在飞轮次」初值：输入快照（价/持仓/信号板/体制/共识/证据摘要）+ 空 thinking/analysis + deciding 态。"""
    return {
        "round": round_no, "ts": ts, "lastPx": ref_px,
        "position": payload.get("position"),
        "signalBoard": signal_board,
        "marketContext": payload.get("marketContext"),
        "multiTimeframeContext": payload.get("multiTimeframeContext"),
        "consensus": payload.get("consensus"),
        "marketDigest": payload.get("marketDigest"),   # R1：round_start/trace 承载证据摘要（RC-9）
        "trigger": payload.get("trigger"),   # wake-interval：唤醒来源 candle/interval（round_start 轨迹带来源）
        "thinking": "", "analysis": "",
        "toolCallPending": False, "toolCall": None,
        "executed": None, "degradeReason": None,
        "summary": None, "errorMessage": None,
        "retries": 0, "lastRetryReason": None,   # §4.4：本轮重试计数 + 最近一次触发原因
        # 下单前二次反思（ai-trade-pre-trade-reflection §5.2）：仅交易意愿轮被写入，hold/关闭轮维持初值。
        "reflection": "", "reflectionPending": False,
        "reflectionVerdict": None, "reflectionReason": None, "reflectionAdjustedSize": None,
        # 【基础策略】铁律门禁（ai-trade-base-strategy-ma-band §2.9）：规则未装配的会话恒为初值。
        "baseRuleStatus": None, "baseRuleZone": None,
        "baseRulePctB": None, "baseRuleReason": None,
        # 最小 edge 门禁（ai-trade-cost-accounting-and-edge-gate §4.3）：k_min=0（默认）的会话恒为初值。
        "edgeGateStatus": None, "edgeBps": None,
        "edgeCostBps": None, "edgeGateReason": None,
        # 信号先验（signal-prior-and-ev-gate §3.10）：**加法键**，不新增 trace phase（新增一个相位
        # 的代价是前端 6 处镜像点）。三总闸全关的会话恒为初值 None ⇒ 前端整段回退旧文案。
        "signalPriors": payload.get("signalPriors"),
        "edgeSource": None, "priorP": None, "priorEvBps": None,
        # 利润保护快照（ai-trade-profit-protection §2.7）：**刻意不新增 trace phase**——基础策略是逐轮
        # 门禁故每轮一条事件，保护层跑在 5s 盯市上、不是轮级判定，只随 round_start 带一份断面。
        "profitGuard": payload.get("profitGuard"),
        # 冷却期（adaptive §4.2 三载体之二）：本函数是**显式取键**装配，不加这一行 `AiTradeRoundTrace`
        # 永远拿不到冷却数据 ⇒ 轨迹上「本轮为什么被降级」永远空白（跑不出错、功能静默缺失）。
        "profitGuardCooldown": payload.get("profitGuardCooldown"),
        # 仓位 / 刹车层（vol-sizing-and-drawdown-brake §3.9）：本函数是**显式取键**装配，漏抄这批键
        # `AiTradeRoundTrace` 永远拿不到本层数据 ⇒ 后端发什么前端都收不到（跑不出错、功能静默缺失）。
        # 键集合走 `SIZING_TRACE_KEYS` 单一真源，杜绝「事件加了键、初值忘了加」的漂移。
        **{key: None for key in SIZING_TRACE_KEYS},
        "state": "deciding",
    }


def trace_event(phase: str, round_no: int, ts: int, **data) -> dict:
    """构造 `agent_trace` WS 事件信封（沿用既有 `{type,data}`；老前端忽略未知 type，向后兼容）。"""
    return {"type": "agent_trace",
            "data": {"phase": phase, "round": round_no, "ts": ts, **data}}


# ---------- 下单前二次反思轨迹装配（RV-2：下沉 trace.py 控 session.py 增量）----------
def reflection_start_event(round_no: int, ts: int, decision: "TradeDecision") -> dict:
    """reflection_start：进入二次反思，携被复核决策快照（仅交易意愿轮出现）。"""
    return trace_event("reflection_start", round_no, ts, input={
        "action": decision.action, "sizeFraction": decision.size_fraction,
        "confidence": decision.confidence, "reason": decision.reason})


def reflection_verdict_event(round_no: int, ts: int, verdict: "ReflectionVerdict",
                             adjusted_size_fraction: float | None = None) -> dict:
    """reflection_verdict：反思裁决（confirm/abort）+ 实际收缩后仓位（若有）+ 理由。"""
    return trace_event("reflection_verdict", round_no, ts, verdict=verdict.verdict,
                       adjustedSizeFraction=adjusted_size_fraction, reason=verdict.reason)


# ---------- 【基础策略】铁律门禁轨迹装配（ai-trade-base-strategy-ma-band §2.9）----------
def base_rule_event(round_no: int, ts: int, band: dict | None,
                    status: str, reason: str | None = None) -> dict:
    """base_rule：本轮铁律判定（pass/veto/unavailable）+ 通道断面 + 降级原因。

    静默的保守会被用户当成故障（`ai-trade-reflection-hold-inertia` 是本仓库的前科），故**每轮**都发一条
    ——通道位置与放行 / 否决必须在 UI 上自解释。`band is None` 时三条通道线为 None（不假数据）。
    """
    band = band or {}
    return trace_event("base_rule", round_no, ts, status=status,
                       zone=band.get("zone"), pctB=band.get("pctB"),
                       upper=band.get("upper"), mid=band.get("mid"),
                       lower=band.get("lower"), reason=reason)


# ---------- 最小 edge 门禁轨迹装配（ai-trade-cost-accounting-and-edge-gate §5.3）----------
def edge_gate_event(round_no: int, ts: int, wire: dict,
                    cost_snapshot: dict | None = None) -> dict:
    """edge_gate：本轮门禁判定（pass/veto/unavailable）+ edge/成本断面 + 会话内成本快照。

    与 `base_rule_event` 的理由逐字相同：**每轮都发一条**（`k_min > 0` 时）——静默的保守会被用户
    当成故障。`costSnapshot` 是纯加法只读键（§5.4 的会话内观测通道），前端未识别即忽略。
    """
    return trace_event("edge_gate", round_no, ts, costSnapshot=cost_snapshot, **wire)


def edge_gate_unavailable_event(ts: int, streak: int) -> dict:
    """连续不可用告警（镜像 `AI_TRADE_BASE_RULE_UNAVAILABLE` 的既有写法）。

    极性与铁律层相反：本层 fail-open，**期间开仓一律放行**——静默的降级与静默的保守一样，都会被
    用户当成故障，故文案必须把「放行」这件事讲清楚，而不是让用户以为门禁在生效。
    """
    return {"type": "error", "data": {
        "ts": int(ts), "code": "AI_TRADE_EDGE_GATE_UNAVAILABLE",
        "message": (f"最小 edge 门禁已连续 {streak} 轮无法估算（ATR 数据不足），"
                    "期间开仓一律**放行**（本层 fail-open）；平仓与撤单不受影响")}}


def apply_edge_gate_trace(trace: dict | None, wire: dict, reason: str | None) -> None:
    """把本轮 edge 门禁判定写入在飞轮次（`trace is None` ⇒ 空转，与既有 emit 段同款垫片）。"""
    if trace is None:
        return
    trace["edgeGateStatus"] = wire.get("status")
    trace["edgeBps"] = wire.get("edgeBps")
    trace["edgeCostBps"] = wire.get("costBps")
    trace["edgeGateReason"] = reason
    trace["edgeSource"] = wire.get("edgeSource")   # 前端据此改判「分子是怎么来的」文案
    trace["priorP"] = wire.get("priorP")
    trace["priorEvBps"] = wire.get("priorEvBps")


# ---------- 仓位 / 刹车层轨迹装配（ai-trade-vol-sizing-and-drawdown-brake §3.9）----------
def sizing_event(round_no: int, ts: int, wire: dict) -> dict:
    """sizing：本轮仓位求解 + ATR 止损 + 刹车断面（规则装配时**每轮一条**）。

    与 `base_rule_event` / `edge_gate_event` 的理由逐字相同：静默的保守会被用户当成故障。本层尤其
    需要每轮可见——`fFinalRaw` 与 `finalFraction` 之差就是被档位地板吃掉的量，只看后者会让运维以为
    减速生效了（[P1-2]）。
    """
    return trace_event("sizing", round_no, ts,
                       **{key: wire.get(key) for key in SIZING_TRACE_KEYS})


def apply_sizing_trace(trace: dict | None, wire: dict) -> None:
    """把本轮仓位判定写入在飞轮次（`trace is None` ⇒ 空转，与既有 emit 段同款垫片）。"""
    if trace is None:
        return
    for key in SIZING_TRACE_KEYS:
        trace[key] = wire.get(key)
